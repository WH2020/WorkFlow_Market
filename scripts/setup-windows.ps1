[CmdletBinding()]
param(
    [switch]$SkipDependencies,
    [switch]$SkipPiInstall,
    [switch]$RequirePpt
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
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command corepack -ErrorAction SilentlyContinue)) {
        throw "pnpm was not found and Corepack is unavailable. Install pnpm 9+ and retry."
    }
    Invoke-Checked -FilePath (Get-Command corepack).Source -Arguments @("enable")
    Invoke-Checked -FilePath (Get-Command corepack).Source -Arguments @("prepare", "pnpm@10", "--activate")
}
if (-not $SkipDependencies) {
    Invoke-Checked -FilePath (Get-Command pnpm).Source -Arguments @("install", "--frozen-lockfile")
}
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
Invoke-ProjectPython @("-m", "agent_platform", "doctor")

$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $PythonCommand.Source @PythonPrefix -m agent_platform doctor --require-ppt *> $null
    $PptReady = $LASTEXITCODE -eq 0
} finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}
if ($PptReady) {
    Write-Host "PPT runtime detected. The start script will inject it only into the Pi process." -ForegroundColor Green
} elseif ($RequirePpt) {
    Invoke-ProjectPython @("-m", "agent_platform", "doctor", "--require-ppt")
} else {
    Write-Warning "Core Agent is ready, but the Codex PPT runtime was not detected. Run 'python -m agent_platform doctor --require-ppt' after installing or opening Codex Desktop."
}

Write-Host "Setup complete. Start the Agent with: .\scripts\start-windows.ps1" -ForegroundColor Green
Write-Host "Start the local workbench in another terminal with: python ui/server.py"
