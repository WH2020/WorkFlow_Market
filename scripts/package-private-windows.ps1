[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$PythonEmbedArchive,
    [string]$PythonEmbedSha256,
    [switch]$SkipDataInitialization
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PackageRoot = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $PackageRoot) { throw "Destination must not exist; existing data is never overwritten: $PackageRoot" }
$Parent = Split-Path -Parent $PackageRoot
if (-not (Test-Path -LiteralPath $Parent -PathType Container)) { throw "Destination parent must already exist." }
$Python = (Get-Command python -ErrorAction Stop).Source
$Pnpm = (Get-Command pnpm.exe -ErrorAction Stop).Source
if ($Python -match 'codex-runtimes' -or $Pnpm -match 'codex-runtimes') { throw "Independent Python and pnpm installations are required." }
$HasEmbedArchive = -not [string]::IsNullOrWhiteSpace($PythonEmbedArchive)
$HasEmbedHash = -not [string]::IsNullOrWhiteSpace($PythonEmbedSha256)
if ($HasEmbedArchive -ne $HasEmbedHash) { throw "PythonEmbedArchive and PythonEmbedSha256 must be provided together." }
$EmbedArchivePath = $null
if ($HasEmbedArchive) {
    if ($PythonEmbedSha256 -notmatch '^[a-fA-F0-9]{64}$') { throw "PythonEmbedSha256 must be a 64-character SHA-256 value." }
    $EmbedArchivePath = (Resolve-Path -LiteralPath $PythonEmbedArchive -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $EmbedArchivePath -PathType Leaf)) { throw "Python embeddable archive must be a file." }
    if ((Get-Item -LiteralPath $EmbedArchivePath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Python embeddable archive must not be a link." }
    $ActualEmbedHash = (Get-FileHash -LiteralPath $EmbedArchivePath -Algorithm SHA256).Hash
    if (-not [string]::Equals($ActualEmbedHash, $PythonEmbedSha256, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Python embeddable archive SHA-256 mismatch."
    }
    $BuilderVersion = & $Python -c 'import struct,sys; print(f"{sys.version_info.major}.{sys.version_info.minor}:{struct.calcsize(''P'') * 8}")'
    if ($LASTEXITCODE -ne 0 -or $BuilderVersion.Trim() -ne '3.11:64') {
        throw "Python 3.11 x64 is required on the build machine for embeddable dependency installation."
    }
}

Push-Location -LiteralPath $ProjectRoot
try {
    # The privacy helper creates one new leaf with an owner-only protected DACL
    # and validates the entire ancestor chain. It never repairs an existing path.
    & $Python -c 'import sys; from pathlib import Path; from agent_platform.wechat_privacy import ensure_private_directory; p=Path(sys.argv[1]); assert not p.exists(); ensure_private_directory(p)' $PackageRoot
    if ($LASTEXITCODE -ne 0) { throw "Private package directory creation failed." }
    $TrackedFiles = @(& git -c core.quotepath=false ls-files --cached)
    if ($LASTEXITCODE -ne 0) { throw "Cannot enumerate package source files." }
    # Untracked inputs are never discovered broadly. Every entry below was
    # reviewed as source/config needed by the WX, CLI or app update release.
    $ReviewedUntrackedFiles = @(
        'agent_platform/app_updates.py',
        'agent_platform/cli_model_catalog.py',
        'agent_platform/cli_process_host.py',
        'agent_platform/cli_provider.py',
        'agent_platform/local_http_security.py',
        'agent_platform/wechat_privacy.py',
        'agent_platform/wxdecipher.py',
        'agent_platform/wxdecipher_capture.py',
        'agent_platform/wxdecipher_crypto.py',
        'agent_platform/wxdecipher_media.py',
        'agent_platform/wxdecipher_reader.py',
        'agent_platform/wxdecipher_wal.py',
        'pi/extensions/cli-model-provider.ts',
        'ui/wxdecipher.css',
        'requirements-wxdecipher.txt',
        'desktop/private-runtime.marker',
        'desktop/python311._pth'
    )
    foreach ($Relative in $ReviewedUntrackedFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $Relative) -PathType Leaf)) {
            throw "Reviewed package source is missing: $Relative"
        }
    }
    $Files = @(
        ($TrackedFiles + $ReviewedUntrackedFiles) |
            Where-Object { $_ -notin @('desktop/private-runtime.marker', 'desktop/python311._pth') } |
            Sort-Object -Unique
    )
    foreach ($Relative in $Files) {
        $Included = $Relative -match '^(agent_platform|profiles|vertical_plugins|pi|plugin/market-director-copilot)/' -or
            $Relative -match '^ui/(server.py|app.js|index.html|styles.css|wxdecipher.css)$' -or
            $Relative -match '^scripts/(start-windows.ps1|coding-agent.ps1)$' -or
            $Relative -match '^library/templates/' -or
            $Relative -match '^data/.+\.example\.(csv|json)$' -or
            $Relative -in @('AGENTS.md', 'LICENSE', 'README.md', 'package.json', 'pnpm-lock.yaml', 'tsconfig.json', 'requirements.txt', 'requirements-wxdecipher.txt')
        $Excluded = $Relative -match '(^|/)(tests|outputs|__pycache__|company|node_modules|\.pi)(/|$)' -or
            $Relative -match '(?i)\.(db|sqlite|sqlite3)(-wal|-shm)?$' -or $Relative.Contains('%')
        if (-not $Included -or $Excluded) { continue }
        $Source = Join-Path $ProjectRoot $Relative
        if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { continue }
        if ((Get-Item -LiteralPath $Source -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Source link is not allowed: $Relative" }
        $Target = Join-Path $PackageRoot $Relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Target
    }
    $NodeSource = Join-Path $ProjectRoot 'runtime\node'
    if (-not (Test-Path -LiteralPath (Join-Path $NodeSource 'node.exe') -PathType Leaf)) { throw 'Pinned project Node runtime is missing.' }
    $NodeVersion = & (Join-Path $NodeSource 'node.exe') --version
    if ($LASTEXITCODE -ne 0 -or $NodeVersion.Trim() -ne 'v24.19.0') { throw 'Pinned project Node runtime must be v24.19.0.' }
    $NodeLinks = @(Get-ChildItem -LiteralPath $NodeSource -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
    if ($NodeLinks.Count) { throw 'Portable Node source contains links; rebuild it from the verified upstream archive.' }
    New-Item -ItemType Directory -Path (Join-Path $PackageRoot 'runtime') | Out-Null
    Copy-Item -LiteralPath $NodeSource -Destination (Join-Path $PackageRoot 'runtime\node') -Recurse
    Copy-Item -LiteralPath $Pnpm -Destination (Join-Path $PackageRoot 'runtime\node\pnpm.exe')
    $PnpmDist = Join-Path (Split-Path -Parent $Pnpm) 'dist'
    if (Test-Path -LiteralPath $PnpmDist -PathType Container) {
        $PnpmLinks = @(Get-ChildItem -LiteralPath $PnpmDist -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
        if ($PnpmLinks.Count) { throw 'pnpm distribution contains links.' }
        Copy-Item -LiteralPath $PnpmDist -Destination (Join-Path $PackageRoot 'runtime\node\dist') -Recurse
    }
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'desktop\private-runtime.marker') -Destination (Join-Path $PackageRoot 'runtime\private-runtime.marker')
    Set-Location -LiteralPath $PackageRoot
    $PackagePython = Join-Path $PackageRoot '.venv\Scripts\python.exe'
    if ($HasEmbedArchive) {
        $EmbedScripts = Join-Path $PackageRoot '.venv\Scripts'
        $SitePackages = Join-Path $PackageRoot '.venv\Lib\site-packages'
        New-Item -ItemType Directory -Path $EmbedScripts -Force | Out-Null
        New-Item -ItemType Directory -Path $SitePackages -Force | Out-Null
        Expand-Archive -LiteralPath $EmbedArchivePath -DestinationPath $EmbedScripts
        $EmbedLinks = @(Get-ChildItem -LiteralPath (Join-Path $PackageRoot '.venv') -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
        if ($EmbedLinks.Count) { throw 'Python embeddable archive contains links.' }
        foreach ($RequiredRuntimeFile in @('python.exe', 'python311.dll', 'python311.zip')) {
            if (-not (Test-Path -LiteralPath (Join-Path $EmbedScripts $RequiredRuntimeFile) -PathType Leaf)) {
                throw "Python embeddable archive is missing $RequiredRuntimeFile."
            }
        }
        Copy-Item -LiteralPath (Join-Path $ProjectRoot 'desktop\python311._pth') -Destination (Join-Path $EmbedScripts 'python311._pth') -Force
        $EmbedVersion = & $PackagePython -I -c 'import struct,sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}:{struct.calcsize(''P'') * 8}")'
        if ($LASTEXITCODE -ne 0 -or $EmbedVersion.Trim() -ne '3.11.9:64') {
            throw 'Python embeddable archive must contain Python 3.11.9 x64.'
        }
        & $Python -m pip --isolated install --no-user --index-url https://pypi.org/simple --disable-pip-version-check --only-binary=:all: --no-compile --target $SitePackages -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Embedded Python dependency installation failed.' }
        $SmokeTest = @'
import importlib
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()

def inside(value):
    path = Path(value).resolve()
    return path == root or root in path.parents

assert sys.version_info[:3] == (3, 11, 9)
assert sys.flags.isolated == 1 and sys.flags.no_user_site == 1 and sys.flags.no_site == 1
assert inside(sys.executable) and inside(sys.prefix) and inside(sys.base_prefix)
assert all(inside(item) for item in sys.path if item)
for name in ('json', 'agent_platform', 'faster_whisper', 'pptx', 'PIL', 'yaml', 'Crypto', 'zstandard'):
    module = importlib.import_module(name)
    origin = getattr(module, '__file__', None)
    assert origin and inside(origin), (name, origin)
assert not list((root / '.venv').rglob('*.egg-link'))
assert not list((root / '.venv/Lib/site-packages').rglob('*.pth'))
print('Embedded Python isolation verified')
'@
        $PreviousDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
        $env:PYTHONDONTWRITEBYTECODE = '1'
        try {
            & $PackagePython -B -c $SmokeTest $PackageRoot
        } finally {
            if ($null -eq $PreviousDontWriteBytecode) {
                Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
            } else {
                $env:PYTHONDONTWRITEBYTECODE = $PreviousDontWriteBytecode
            }
        }
        if ($LASTEXITCODE -ne 0) { throw 'Embedded Python isolation or dependency validation failed.' }
    } else {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Private Python environment creation failed.' }
        & $PackagePython -m pip install --disable-pip-version-check -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
        & $PackagePython -m pip check
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency validation failed.' }
    }
    # Reinstall; developer pnpm shims contain absolute paths. Copy package bytes,
    # not hardlinks to the shared store, so later changes cannot alias the package.
    & $Pnpm install --prefer-offline --frozen-lockfile --ignore-scripts --node-linker=hoisted --package-import-method=copy
    if ($LASTEXITCODE -ne 0) { throw 'Node dependency installation failed.' }
    if (@(Get-ChildItem -LiteralPath $PackageRoot -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) {
        throw 'Final package contains reparse points; no installer may be generated.'
    }
    if (-not $SkipDataInitialization) {
        & $PackagePython plugin/market-director-copilot/scripts/init_local_data.py --project $PackageRoot
        if ($LASTEXITCODE -ne 0) { throw 'Empty local data initialization failed.' }
    } else {
        Write-Output 'Local data initialization skipped; the installer must initialize from .example files before enabling the EXE.'
    }
    & $PackagePython -c 'import sys; from agent_platform.wechat_privacy import verify_private_directory; verify_private_directory(sys.argv[1]); print("Private package ACL verified")' $PackageRoot
    if ($LASTEXITCODE -ne 0) { throw 'Final package ACL validation failed.' }
    Write-Output "Runtime prepared: $PackageRoot"
    if ($HasEmbedArchive) {
        Write-Output 'Embedded Python is self-contained; Git, rg, fd and WebView2 remain external prerequisites. Keep the directory intact.'
    } else {
        Write-Output 'This is a same-machine runtime directory: the installed Python base, Git, rg, fd and WebView2 remain external prerequisites. Keep the directory intact.'
    }
} finally {
    Pop-Location
}
