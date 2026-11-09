param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-Location $PSScriptRoot

if ($env:OS -ne "Windows_NT") {
    throw "Build this Windows release on Windows with 64-bit Python 3.11."
}

# Developers can run this directly; CI can pass -Python python after setup-python.
if (-not $Python) {
    $Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $Python)) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3.11 -m venv .venv
        } else {
            & python -m venv .venv
        }
        if ($LASTEXITCODE -ne 0) { throw "Could not create the build environment." }
    }
}
& $Python -c "import struct, sys; assert sys.platform == 'win32' and struct.calcsize('P') == 8, '64-bit Windows Python is required'"
if ($LASTEXITCODE -ne 0) { throw "64-bit Windows Python is required." }
& $Python -m pip install -r requirements.txt "pyinstaller==6.22.3"
if ($LASTEXITCODE -ne 0) { throw "Could not install build dependencies." }

# Pin and verify the official Fixed Version package so end users need neither
# Python nor a separately installed WebView2 runtime, even when running offline.
$runtimeInfo = Get-Content -Raw (Join-Path $PSScriptRoot "scripts\webview2-runtime.json") | ConvertFrom-Json
$cacheRoot = Join-Path $PSScriptRoot "build\webview2"
New-Item -ItemType Directory -Force $cacheRoot | Out-Null
$archive = Join-Path $cacheRoot "$($runtimeInfo.version).$($runtimeInfo.architecture).cab"
if (-not (Test-Path $archive) -or (Get-FileHash $archive -Algorithm SHA256).Hash -ne $runtimeInfo.sha256) {
    Write-Host "Downloading Microsoft WebView2 $($runtimeInfo.version) ($($runtimeInfo.architecture))..."
    & curl.exe --fail --location --retry 3 --connect-timeout 30 --max-time 600 --output $archive $runtimeInfo.url
    if ($LASTEXITCODE -ne 0) { throw "Could not download the fixed WebView2 runtime." }
}
if ((Get-FileHash $archive -Algorithm SHA256).Hash -ne $runtimeInfo.sha256) {
    throw "The WebView2 archive SHA256 does not match scripts/webview2-runtime.json."
}

# Always expand the verified archive; a modified cache must not enter a release.
$extracted = Join-Path $cacheRoot "runtime"
if (Test-Path $extracted) { Remove-Item -LiteralPath $extracted -Recurse -Force }
New-Item -ItemType Directory -Force $extracted | Out-Null
& "$env:SystemRoot\System32\expand.exe" $archive '-F:*' $extracted | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not expand the WebView2 runtime." }
$runtimeExecutables = @(Get-ChildItem -LiteralPath $extracted -Recurse -File -Filter msedgewebview2.exe)
if ($runtimeExecutables.Count -ne 1) { throw "Expected exactly one msedgewebview2.exe in the fixed runtime." }
$runtimeDir = $runtimeExecutables[0].Directory.FullName
$runtimeSignature = Get-AuthenticodeSignature -LiteralPath $runtimeExecutables[0].FullName
if ($runtimeSignature.Status -ne "Valid" -or $runtimeSignature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    throw "The WebView2 executable does not have a valid Microsoft signature."
}

$previousRuntime = $env:RF_WEBVIEW2_RUNTIME
try {
    $env:RF_WEBVIEW2_RUNTIME = $runtimeDir
    & $Python -m PyInstaller --noconfirm --clean remote-folder.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
} finally {
    $env:RF_WEBVIEW2_RUNTIME = $previousRuntime
}

$exe = Join-Path $PSScriptRoot "dist\remote-folder.exe"
if (-not (Test-Path $exe)) { throw "The release EXE was not produced." }
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  remote-folder.exe" | Set-Content -Encoding ascii (Join-Path $PSScriptRoot "dist\SHA256SUMS.txt")
Write-Host "Built $exe"
Write-Host "The single EXE includes Python, application dependencies and WebView2."
