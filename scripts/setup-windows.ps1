[CmdletBinding()]
param(
    [switch]$SkipDependencies,
    [switch]$SkipPiInstall,
    [switch]$SkipLibreOfficeInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ProjectRoot

function Invoke-Checked {
    param([string]$FilePath, [string[]]$Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
$PythonPrefix = @()
if (-not $PythonCommand) {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    $PythonPrefix = @("-3.11")
}
if (-not $PythonCommand) {
    throw "Python 3.11+ was not found. Install Python and run this script again."
}
function Invoke-ProjectPython {
    param([string[]]$Arguments)
    Invoke-Checked -FilePath $PythonCommand.Source -Arguments @($PythonPrefix + $Arguments)
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js 24.19.x was not found. Install the validated Node.js 24.19 runtime and run this script again."
}
$NodeVersionText = (& node --version).Trim().TrimStart("v")
$NodeVersion = $null
if (-not [Version]::TryParse($NodeVersionText, [ref]$NodeVersion) -or $NodeVersion -lt [Version]"24.19.0" -or $NodeVersion -ge [Version]"25.0.0") {
    throw "Unsupported Node.js ${NodeVersionText}. Install Node.js 24.19.0 or newer within the Node 24 line."
}

$CodexPrivatePathPattern = '(?i)([\\/]codex-runtimes[\\/]|[\\/]\.codex[\\/]|[\\/]OpenAI\.Codex_)'
function Update-ProcessPathFromSystem {
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $MachinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $Seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $Merged = New-Object 'System.Collections.Generic.List[string]'
    foreach ($SourcePath in @($env:Path, $UserPath, $MachinePath)) {
        foreach ($Segment in ($SourcePath -split [Regex]::Escape([string][IO.Path]::PathSeparator))) {
            $Normalized = $Segment.Trim()
            if ($Normalized -and $Normalized -notmatch $CodexPrivatePathPattern -and $Seen.Add($Normalized)) {
                $Merged.Add($Normalized)
            }
        }
    }
    $env:Path = $Merged -join [IO.Path]::PathSeparator
}

function Get-IndependentPnpm {
    $Candidate = Get-Command pnpm -ErrorAction SilentlyContinue
    if ($Candidate -and $Candidate.Source -match $CodexPrivatePathPattern) {
        Update-ProcessPathFromSystem
        $Candidate = Get-Command pnpm -ErrorAction SilentlyContinue
    }
    if ($Candidate -and $Candidate.Source -notmatch $CodexPrivatePathPattern) {
        return $Candidate
    }

    $Corepack = Get-Command corepack -ErrorAction SilentlyContinue
    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    $Npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($Corepack) {
        Invoke-Checked -FilePath $Corepack.Source -Arguments @("enable")
        Invoke-Checked -FilePath $Corepack.Source -Arguments @("prepare", "pnpm@10", "--activate")
    } elseif ($Winget) {
        Invoke-Checked -FilePath $Winget.Source -Arguments @(
            "install", "--id", "pnpm.pnpm", "--exact", "--silent",
            "--accept-package-agreements", "--accept-source-agreements"
        )
        Update-ProcessPathFromSystem
    } elseif ($Npm) {
        Invoke-Checked -FilePath $Npm.Source -Arguments @("install", "-g", "pnpm@10")
    } else {
        throw "Independent pnpm was not found. Install pnpm 9+ with winget or the official Node.js installer, then retry."
    }
    $Candidate = Get-Command pnpm -ErrorAction SilentlyContinue
    if (-not $Candidate -or $Candidate.Source -match $CodexPrivatePathPattern) {
        throw "Independent pnpm installation finished, but a non-Codex pnpm command is still unavailable. Restart PowerShell and retry."
    }
    return $Candidate
}
$PnpmCommand = Get-IndependentPnpm
if (-not $SkipDependencies) {
    Invoke-Checked -FilePath $PnpmCommand.Source -Arguments @("install", "--frozen-lockfile", "--ignore-scripts")
}

function Install-IndependentCli {
    param([string]$CommandName, [string]$WingetId, [string]$ChocolateyId)
    $Candidate = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($Candidate -and $Candidate.Source -match $CodexPrivatePathPattern) {
        Update-ProcessPathFromSystem
        $Candidate = Get-Command $CommandName -ErrorAction SilentlyContinue
    }
    if ($Candidate -and $Candidate.Source -notmatch $CodexPrivatePathPattern) { return }
    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    $Chocolatey = Get-Command choco -ErrorAction SilentlyContinue
    if ($Winget) {
        try {
            Invoke-Checked -FilePath $Winget.Source -Arguments @(
                "install", "--id", $WingetId, "--exact", "--silent",
                "--accept-package-agreements", "--accept-source-agreements"
            )
        } catch {
            if (-not $Chocolatey) { throw }
            Invoke-Checked -FilePath $Chocolatey.Source -Arguments @("install", $ChocolateyId, "-y", "--no-progress")
        }
    } elseif ($Chocolatey) {
        Invoke-Checked -FilePath $Chocolatey.Source -Arguments @("install", $ChocolateyId, "-y", "--no-progress")
    } else {
        throw "$CommandName is required. Install $WingetId with winget and retry."
    }
    Update-ProcessPathFromSystem
    $Candidate = Get-Command $CommandName -ErrorAction SilentlyContinue
    if (-not $Candidate) {
        $WingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
        if (Test-Path -LiteralPath $WingetPackages -PathType Container) {
            $InstalledExecutable = Get-ChildItem -LiteralPath $WingetPackages -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like "$WingetId`_*" } |
                Sort-Object LastWriteTime -Descending |
                ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Filter "$CommandName.exe" -File -Recurse -ErrorAction SilentlyContinue } |
                Select-Object -First 1
            if ($InstalledExecutable) {
                $env:Path = $InstalledExecutable.DirectoryName + [IO.Path]::PathSeparator + $env:Path
                $Candidate = Get-Command $CommandName -ErrorAction SilentlyContinue
            }
        }
    }
    if (-not $Candidate -or $Candidate.Source -match $CodexPrivatePathPattern) {
        throw "$CommandName installation completed, but an independent executable is unavailable. Restart PowerShell and retry."
    }
}
Install-IndependentCli -CommandName "rg" -WingetId "BurntSushi.ripgrep.MSVC" -ChocolateyId "ripgrep"
Install-IndependentCli -CommandName "fd" -WingetId "sharkdp.fd" -ChocolateyId "fd"

$LibreOfficeCandidates = @(
    (Join-Path ${env:ProgramFiles} "LibreOffice\program\soffice.com"),
    (Join-Path ${env:ProgramFiles} "LibreOffice\program\soffice.exe")
)
$LibreOfficePath = $LibreOfficeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $LibreOfficePath) {
    $LibreOfficeCommand = Get-Command soffice -ErrorAction SilentlyContinue
    if ($LibreOfficeCommand) { $LibreOfficePath = $LibreOfficeCommand.Source }
}
if (-not $LibreOfficePath) {
    if ($SkipLibreOfficeInstall) {
        throw "LibreOffice was not found. Remove -SkipLibreOfficeInstall or install LibreOffice first."
    }
    $WingetCommand = Get-Command winget -ErrorAction SilentlyContinue
    $ChocolateyCommand = Get-Command choco -ErrorAction SilentlyContinue
    if ($WingetCommand) {
        try {
            Invoke-Checked -FilePath $WingetCommand.Source -Arguments @(
                "install", "--id", "TheDocumentFoundation.LibreOffice", "--exact", "--silent",
                "--accept-package-agreements", "--accept-source-agreements"
            )
        } catch {
            if (-not $ChocolateyCommand) { throw }
            Write-Warning "winget could not install LibreOffice; retrying with Chocolatey."
            Invoke-Checked -FilePath $ChocolateyCommand.Source -Arguments @("install", "libreoffice-fresh", "-y", "--no-progress")
        }
    } elseif ($ChocolateyCommand) {
        Invoke-Checked -FilePath $ChocolateyCommand.Source -Arguments @("install", "libreoffice-fresh", "-y", "--no-progress")
    } else {
        throw "LibreOffice is required for independent PPT rendering. Install it from libreoffice.org and retry."
    }
    $LibreOfficePath = $LibreOfficeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $LibreOfficePath) { throw "LibreOffice installation completed but soffice was not found in the standard location." }
}
$env:WORKFLOW_LIBREOFFICE_PATH = $LibreOfficePath
$PiCommand = Get-Command pi -ErrorAction SilentlyContinue
if (-not $PiCommand) {
    $LocalPi = Join-Path $ProjectRoot "node_modules\.bin\pi.cmd"
    if (Test-Path -LiteralPath $LocalPi -PathType Leaf) {
        $PiPath = $LocalPi
    } elseif ($SkipPiInstall) {
        throw "Pi was not found. Remove -SkipPiInstall or install project dependencies first."
    } else {
        $NpmCommand = Get-Command npm -ErrorAction SilentlyContinue
        if (-not $NpmCommand) {
            throw "npm was not found, so the Pi fallback install cannot continue."
        }
        Invoke-Checked -FilePath $NpmCommand.Source -Arguments @(
            "install", "-g", "--ignore-scripts", "@earendil-works/pi-coding-agent@0.84.2"
        )
        $PiPath = (Get-Command pi -ErrorAction Stop).Source
    }
} else {
    $PiPath = $PiCommand.Source
}
Invoke-ProjectPython @("plugin/market-director-copilot/scripts/init_local_data.py", "--project", ".")
Invoke-ProjectPython @("-m", "agent_platform", "configure-subagents")
Invoke-ProjectPython @("-m", "agent_platform", "validate")
Invoke-Checked -FilePath $PiPath -Arguments @("install", "-l", ".", "--approve")
$DoctorJson = @(Invoke-ProjectPython @("-m", "agent_platform", "doctor", "--require-ppt"))
$DoctorJson | ForEach-Object { Write-Output $_ }
$DoctorResult = ($DoctorJson -join [Environment]::NewLine) | ConvertFrom-Json
if (-not [bool]$DoctorResult.ppt.ready) { throw "Independent PPT runtime validation failed." }
Write-Host "Independent PPT runtime detected: PptxGenJS + LibreOffice + PDF.js." -ForegroundColor Green
$DesktopBuild = Join-Path $ProjectRoot "scripts\build-windows-desktop.ps1"
& $DesktopBuild
if ($LASTEXITCODE -ne 0) { throw "Agent4Market.exe build failed." }

Write-Host "Setup complete. Double-click Agent4Market.exe to open the Sales Director desktop app." -ForegroundColor Green
exit 0
