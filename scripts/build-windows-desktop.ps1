[CmdletBinding()]
param(
    [string]$OutputPath,
    [switch]$SkipSelfTest
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Manifest = Join-Path $ProjectRoot "desktop\src-tauri\Cargo.toml"
if (-not $OutputPath) { $OutputPath = Join-Path $ProjectRoot "Agent4Market.exe" }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$Cargo = Get-Command cargo -ErrorAction SilentlyContinue
if (-not $Cargo) { throw "Rust/Cargo was not found. Install rustup and the Microsoft C++ Build Tools, then retry." }
if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) { throw "Tauri desktop manifest is missing: $Manifest" }

& $Cargo.Source build --manifest-path $Manifest --release --locked
if ($LASTEXITCODE -ne 0) { throw "Tauri desktop compilation failed with exit code $LASTEXITCODE." }
$BuiltExecutable = Join-Path $ProjectRoot "desktop\src-tauri\target\release\Agent4Market.exe"
if (-not (Test-Path -LiteralPath $BuiltExecutable -PathType Leaf)) {
    throw "Tauri desktop executable was not produced: $BuiltExecutable"
}
$OutputDirectory = Split-Path -Parent $OutputPath
if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}
Copy-Item -LiteralPath $BuiltExecutable -Destination $OutputPath -Force

if (-not $SkipSelfTest) {
    & $OutputPath --self-test
    if ($LASTEXITCODE -ne 0) {
        $LauncherLog = Join-Path $ProjectRoot ".pi\director-runtime\desktop-launcher.log"
        $Diagnostic = if (Test-Path -LiteralPath $LauncherLog -PathType Leaf) {
            (@(Get-Content -LiteralPath $LauncherLog -Tail 20 -ErrorAction SilentlyContinue) -join " | ")
        } else { "no desktop launcher log" }
        throw "Tauri desktop self-test failed with exit code ${LASTEXITCODE}: $Diagnostic"
    }
}

$File = Get-Item -LiteralPath $OutputPath
Write-Output ([pscustomobject]@{
    status = "ok"
    shell = "Tauri 2 + WebView2"
    edition = "sales-director"
    path = $File.FullName
    version = $File.VersionInfo.FileVersion
    bytes = $File.Length
    sha256 = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
} | ConvertTo-Json -Compress)
