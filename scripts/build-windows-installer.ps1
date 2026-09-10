[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RuntimeDirectory,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [string]$MakeNsisPath = (Join-Path $env:LOCALAPPDATA 'tauri\NSIS\makensis.exe'),
    [string]$ExpectedManifestPath,
    [string]$ExpectedManifestSha256
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PayloadRoot = (Resolve-Path -LiteralPath $RuntimeDirectory).Path
if ((Split-Path -Leaf $PayloadRoot) -notmatch '^Agent4Market-\d+\.\d+\.\d+-payload-\d{8}$') {
    throw 'Installer input must be an explicitly prepared release payload directory, never an installed application.'
}
$InstallerPath = [IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $InstallerPath) { throw 'Installer output already exists; choose a new output file.' }
$HasExpectedManifest = -not [string]::IsNullOrWhiteSpace($ExpectedManifestPath)
if ($HasExpectedManifest -ne (-not [string]::IsNullOrWhiteSpace($ExpectedManifestSha256))) {
    throw 'ExpectedManifestPath and ExpectedManifestSha256 must be provided together.'
}
if ($HasExpectedManifest) {
    if ($ExpectedManifestSha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid expected manifest SHA-256.' }
    $ExpectedManifestFile = Get-Item -LiteralPath $ExpectedManifestPath
    if ($ExpectedManifestFile.PSIsContainer -or $ExpectedManifestFile.Length -gt 33554432 -or
        ($ExpectedManifestFile.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Invalid expected manifest file.' }
    if ((Get-FileHash -LiteralPath $ExpectedManifestPath -Algorithm SHA256).Hash -ne $ExpectedManifestSha256) {
        throw 'Expected manifest differs from the reviewed payload plan.'
    }
}
if (-not (Test-Path -LiteralPath $MakeNsisPath -PathType Leaf)) { throw 'NSIS 3.x compiler was not found.' }
if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot '.venv\Scripts\python311._pth'))) { throw 'A portable embedded-Python release payload is required.' }
$Package = Get-Content -LiteralPath (Join-Path $ProjectRoot 'package.json') -Raw | ConvertFrom-Json
$Version = $Package.version
$PayloadPackage = Get-Content -LiteralPath (Join-Path $PayloadRoot 'package.json') -Raw | ConvertFrom-Json
if ($PayloadPackage.version -ne $Version) { throw 'Payload version does not match source version.' }
foreach ($ForbiddenPayloadEntry in @('.pi', 'outputs', 'library\templates\company', 'agent_platform\%SystemDrive%')) {
    if (Test-Path -LiteralPath (Join-Path $PayloadRoot $ForbiddenPayloadEntry)) { throw "Payload is not clean: $ForbiddenPayloadEntry" }
}
$BuildRoot = Join-Path (Split-Path -Parent $InstallerPath) ('nsis-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $BuildRoot | Out-Null
$Python = Join-Path $PayloadRoot '.venv\Scripts\python.exe'
& $Python (Join-Path $PSScriptRoot 'windows-installer-manifest.py') --payload $PayloadRoot --output $BuildRoot --version $Version
if ($LASTEXITCODE -ne 0) { throw 'Installer manifest validation failed.' }
if ($HasExpectedManifest -and
    (Get-FileHash -LiteralPath (Join-Path $BuildRoot 'install-manifest.json') -Algorithm SHA256).Hash -ne $ExpectedManifestSha256) {
    throw 'Payload drift detected: actual installer manifest differs from the reviewed closed plan.'
}
& $MakeNsisPath /V2 /INPUTCHARSET UTF8 "/DVERSION=$Version" "/DPAYLOAD_ROOT=$PayloadRoot" "/DSOURCE_ROOT=$ProjectRoot" "/DBUILD_ROOT=$BuildRoot" "/DOUTPUT_FILE=$InstallerPath" (Join-Path $ProjectRoot 'desktop\windows-installer.nsi')
if ($LASTEXITCODE -ne 0) { throw 'NSIS installer compilation failed.' }
$Installer = Get-Item -LiteralPath $InstallerPath
[pscustomobject]@{
    status = 'ok'; version = $Version; path = $Installer.FullName; bytes = $Installer.Length
    sha256 = (Get-FileHash -LiteralPath $Installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    manifest = (Join-Path $BuildRoot 'install-manifest.json')
} | ConvertTo-Json -Compress
