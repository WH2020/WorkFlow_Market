param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("codex", "claude")]
    [string]$Agent,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AgentArguments
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Candidates = New-Object 'System.Collections.Generic.List[string]'

Get-Command $Agent -All -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Source -and -not $Candidates.Contains($_.Source)) { $Candidates.Add($_.Source) }
}
if ($env:APPDATA) {
    foreach ($Suffix in @(".cmd", ".exe", ".ps1")) {
        $Candidate = Join-Path $env:APPDATA "npm\$Agent$Suffix"
        if ((Test-Path -LiteralPath $Candidate -PathType Leaf) -and -not $Candidates.Contains($Candidate)) {
            $Candidates.Add($Candidate)
        }
    }
}
$LocalCandidate = Join-Path $env:USERPROFILE ".local\bin\$Agent.exe"
if ((Test-Path -LiteralPath $LocalCandidate -PathType Leaf) -and -not $Candidates.Contains($LocalCandidate)) {
    $Candidates.Add($LocalCandidate)
}

$Selected = $null
foreach ($Candidate in $Candidates) {
    try {
        $VersionOutput = & $Candidate --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $Selected = $Candidate
            break
        }
    } catch {
        continue
    }
}
if (-not $Selected) {
    if ($Agent -eq "codex") {
        throw "No runnable standalone Codex CLI was found. Install Codex CLI from the official OpenAI instructions; Codex Desktop alone may not expose a callable CLI."
    }
    throw "No runnable Claude Code installation was found. Install Claude Code from the official Anthropic instructions."
}

Set-Location -LiteralPath $ProjectRoot
& $Selected @AgentArguments
exit $LASTEXITCODE
