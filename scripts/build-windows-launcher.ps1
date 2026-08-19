[CmdletBinding()]
param(
    [string]$OutputPath,
    [switch]$SkipSelfTest
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SourcePath = Join-Path $ProjectRoot "launcher\Agent4MarketLauncher.cs"
if (-not $OutputPath) { $OutputPath = Join-Path $ProjectRoot "Agent4Market.exe" }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$CompilerCandidates = @(
    (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe")
)
$Compiler = $CompilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $Compiler) { throw "The Windows .NET Framework C# compiler was not found. Enable .NET Framework 4.x and retry." }
if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) { throw "Launcher source is missing: $SourcePath" }
$OutputDirectory = Split-Path -Parent $OutputPath
if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

& $Compiler /nologo /target:exe /platform:x64 /optimize+ "/out:$OutputPath" $SourcePath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
    throw "Agent4Market.exe compilation failed with exit code $LASTEXITCODE."
}
if (-not $SkipSelfTest) {
    $SelfTestOutput = @(& $OutputPath --self-test 2>&1)
    $SelfTestExitCode = $LASTEXITCODE
    $SelfTestOutput | ForEach-Object { Write-Output $_ }
    if ($SelfTestExitCode -ne 0) {
        $LauncherLog = Join-Path $ProjectRoot ".pi\director-runtime\launcher.log"
        $Diagnostic = ($SelfTestOutput -join " ").Trim()
        if (Test-Path -LiteralPath $LauncherLog -PathType Leaf) {
            $LogTail = @(Get-Content -LiteralPath $LauncherLog -Tail 20 -ErrorAction SilentlyContinue)
            if ($LogTail.Count -gt 0) {
                $Diagnostic = ($Diagnostic + " launcher.log: " + ($LogTail -join " | ")).Trim()
            }
        }
        throw "Agent4Market.exe self-test failed with exit code ${SelfTestExitCode}: $Diagnostic"
    }
}
$File = Get-Item -LiteralPath $OutputPath
Write-Output ([pscustomobject]@{
    status = "ok"
    path = $File.FullName
    bytes = $File.Length
    sha256 = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
} | ConvertTo-Json -Compress)
