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
    throw "Node.js 22.19+ was not found. Install Node.js and run this script again."
}

function Get-IndependentPnpm {
    $Candidate = Get-Command pnpm -ErrorAction SilentlyContinue
    if ($Candidate -and $Candidate.Source -match '(?i)([\\/]codex-runtimes[\\/]|[\\/]\.codex[\\/])') {
        $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $MachinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $env:Path = @($UserPath, $MachinePath) -join [IO.Path]::PathSeparator
        $Candidate = Get-Command pnpm -ErrorAction SilentlyContinue
    }
    if ($Candidate -and $Candidate.Source -notmatch '(?i)([\\/]codex-runtimes[\\/]|[\\/]\.codex[\\/])') {
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
        $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $MachinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $env:Path = @($UserPath, $MachinePath) -join [IO.Path]::PathSeparator
    } elseif ($Npm) {
        Invoke-Checked -FilePath $Npm.Source -Arguments @("install", "-g", "pnpm@10")
    } else {
        throw "Independent pnpm was not found. Install pnpm 9+ with winget or the official Node.js installer, then retry."
    }
    $Candidate = Get-Command pnpm -ErrorAction SilentlyContinue
    if (-not $Candidate -or $Candidate.Source -match '(?i)([\\/]codex-runtimes[\\/]|[\\/]\.codex[\\/])') {
        throw "Independent pnpm installation finished, but a non-Codex pnpm command is still unavailable. Restart PowerShell and retry."
    }
    return $Candidate
}
$PnpmCommand = Get-IndependentPnpm
if (-not $SkipDependencies) {
    Invoke-Checked -FilePath $PnpmCommand.Source -Arguments @("install", "--frozen-lockfile", "--ignore-scripts")
}
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
Invoke-ProjectPython @("-m", "agent_platform", "validate")
Invoke-Checked -FilePath $PiPath -Arguments @("install", "-l", ".", "--approve")
$DoctorJson = @(Invoke-ProjectPython @("-m", "agent_platform", "doctor", "--require-ppt"))
$DoctorJson | ForEach-Object { Write-Output $_ }
$DoctorResult = ($DoctorJson -join [Environment]::NewLine) | ConvertFrom-Json
if (-not [bool]$DoctorResult.ppt.ready) { throw "Independent PPT runtime validation failed." }
Write-Host "Independent PPT runtime detected: PptxGenJS + LibreOffice + PDF.js." -ForegroundColor Green

Write-Host "Setup complete. Start the Agent with: .\scripts\start-windows.ps1" -ForegroundColor Green
Write-Host "Start the local workbench in another terminal with: python ui/server.py"
exit 0
