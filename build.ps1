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

& $Python -m PyInstaller --noconfirm --clean remote-folder.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$exe = Join-Path $PSScriptRoot "dist\remote-folder.exe"
if (-not (Test-Path $exe)) { throw "The release EXE was not produced." }
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  remote-folder.exe" | Set-Content -Encoding ascii (Join-Path $PSScriptRoot "dist\SHA256SUMS.txt")
Write-Host "Built $exe"
Write-Host "The single EXE includes Python and application dependencies; it uses the system WebView2 runtime."
