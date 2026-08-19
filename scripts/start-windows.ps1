[CmdletBinding()]
param(
    [switch]$KeepOpen,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PiArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ProjectRoot
$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
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
