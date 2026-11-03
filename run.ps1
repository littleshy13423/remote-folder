$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
chcp 65001 > $null

function Find-Python {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    $commands = Get-Command python -All -ErrorAction SilentlyContinue
    foreach ($command in $commands) {
        if ($command.Source -and $command.Source -notmatch "WindowsApps") {
            return $command.Source
        }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host "Python 3 was not found. Installing Python 3.12..."
    winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
    $python = Find-Python
}
if (-not $python) {
    Write-Host "Install Python 3.12, then run this script again."
    Read-Host "Press Enter to close"
    exit 1
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & $python -m venv (Join-Path $PSScriptRoot ".venv")
}

& $venvPython -c "import webview, fastapi, paramiko"
if ($LASTEXITCODE -ne 0) {
    & $venvPython -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        & $venvPython -m pip install -r (Join-Path $PSScriptRoot "requirements.txt") -i https://pypi.tuna.tsinghua.edu.cn/simple
        if ($LASTEXITCODE -ne 0) {
            Read-Host "Dependency install failed. Press Enter to close"
            exit 1
        }
    }
}

$pythonw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $venvPython }
Start-Process -FilePath $pythonw -ArgumentList "-m", "app" -WorkingDirectory $PSScriptRoot
