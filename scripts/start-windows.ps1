[CmdletBinding()]
param(
    [switch]$KeepOpen,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PiArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ProjectRoot
$PortableNodeDirectory = Join-Path $ProjectRoot "runtime\node"
$PortableNodeExecutable = Join-Path $PortableNodeDirectory "node.exe"
$PrivateRuntimeMarker = Join-Path $ProjectRoot "runtime\private-runtime.marker"
$PrivateRuntime = Test-Path -LiteralPath $PrivateRuntimeMarker
$LocalPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if ($PrivateRuntime) {
    if ((Get-Content -LiteralPath $PrivateRuntimeMarker -Raw).Trim() -ne "Agent4Market private runtime v1") {
        throw "Invalid private runtime marker. Rebuild the private package."
    }
    foreach ($Dependency in @($PortableNodeExecutable, $LocalPython)) {
        if (-not (Test-Path -LiteralPath $Dependency -PathType Leaf)) {
            throw "Private runtime dependency is missing: $Dependency. System fallback is disabled."
        }
    }
    $env:PI_CODING_AGENT_DIR = Join-Path $ProjectRoot ".pi\agent"
    $env:PYTHONNOUSERSITE = "1"
    foreach ($Variable in @("PYTHONPATH", "PYTHONHOME", "NODE_PATH", "NODE_OPTIONS")) {
        [Environment]::SetEnvironmentVariable($Variable, $null, "Process")
    }
    $env:Path = (Join-Path $ProjectRoot ".venv\Scripts") + [IO.Path]::PathSeparator + (Join-Path $ProjectRoot "node_modules\.bin") + [IO.Path]::PathSeparator + $env:Path
}
if (Test-Path -LiteralPath $PortableNodeExecutable -PathType Leaf) {
    $PortableNodeMetadata = Get-Item -LiteralPath $PortableNodeExecutable -Force
    if ($PortableNodeMetadata.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Portable Node.js runtime cannot be a symbolic link. Run setup-windows.ps1 again."
    }
    $env:Path = $PortableNodeDirectory + [IO.Path]::PathSeparator + $env:Path
}
$PythonCommand = if (Test-Path -LiteralPath $LocalPython -PathType Leaf) {
    Get-Command $LocalPython
} else { Get-Command python -ErrorAction SilentlyContinue }
$PythonPrefix = @()
if (-not $PythonCommand) {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    $PythonPrefix = @("-3.11")
}
if (-not $PythonCommand) {
    throw "Python 3.11+ was not found. Run setup-windows.ps1 first."
}
& $PythonCommand.Source @PythonPrefix -m agent_platform launch -- @PiArgs
$AgentExitCode = $LASTEXITCODE
if ($KeepOpen) {
    if ($AgentExitCode -ne 0) {
        Write-Host "`nAI core exited with code $AgentExitCode. Review the message above, then close this window and restart Agent4Market." -ForegroundColor Red
    }
    return
}
exit $AgentExitCode
