$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "未找到 .venv。请先运行 run.bat，再执行本脚本。"
    exit 1
}

& $python -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        Write-Host "PyInstaller 安装失败。"
        exit 1
    }
}

& $python -m PyInstaller --noconfirm --clean remote-folder.spec
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$dist = Join-Path $PSScriptRoot "dist\remote-folder"
$zip = Join-Path $PSScriptRoot "dist\remote-folder-windows.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $dist -DestinationPath $zip
Write-Host "已生成 $zip"
Write-Host "解压后运行 remote-folder\remote-folder.exe"
