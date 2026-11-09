[CmdletBinding()]
param(
    [string]$ExePath = (Join-Path $PSScriptRoot '..\dist\remote-folder.exe'),
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
$sourceExe = (Resolve-Path -LiteralPath $ExePath).Path
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('remote-folder-smoke-' + [guid]::NewGuid().ToString('N'))
$appDir = Join-Path $testRoot 'standalone app'
$extractDir = Join-Path $testRoot 'extracted'
$appHome = Join-Path $testRoot 'data'
$logPath = Join-Path $appHome 'app.log'
$exe = Join-Path $appDir 'remote-folder.exe'
$process = $null
$window = $null
$variables = @('RF_HOME', 'RF_PORT', 'RF_SYNC_DISABLE', 'PYTHONPATH', 'PYTHONHOME', 'PATH', 'TEMP', 'TMP', 'WEBVIEW2_BROWSER_EXECUTABLE_FOLDER', 'PYWEBVIEW_GUI', 'PYTHONNET_RUNTIME')
$savedEnvironment = @{}
foreach ($name in $variables) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

try {
    New-Item -ItemType Directory -Path $appDir, $extractDir, $appHome -Force | Out-Null
    # Only the release EXE is copied. No Python installation, source tree or
    # _internal directory is available through the app's working directory/PATH.
    Copy-Item -LiteralPath $sourceExe -Destination $exe
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    $env:RF_HOME = $appHome
    $env:RF_PORT = [string]$port
    $env:RF_SYNC_DISABLE = '1'
    $env:TEMP = $extractDir
    $env:TMP = $extractDir
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    foreach ($name in @('PYTHONPATH', 'PYTHONHOME', 'WEBVIEW2_BROWSER_EXECUTABLE_FOLDER', 'PYWEBVIEW_GUI', 'PYTHONNET_RUNTIME')) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    $process = Start-Process -FilePath $exe -WorkingDirectory $appDir -PassThru
    $baseUrl = "http://127.0.0.1:$port"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) {
            throw "Release EXE exited before startup completed (exit $($process.ExitCode))."
        }
        $window = Get-Process -Name 'remote-folder' -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $exe -and $_.MainWindowHandle -ne [IntPtr]::Zero -and $_.MainWindowTitle -eq 'remote-folder' } |
            Select-Object -First 1
        # The light release uses Windows' installed WebView2. Follow the EXE's
        # process tree so an unrelated browser cannot make this check pass.
        $snapshot = @(Get-CimInstance Win32_Process)
        $descendantIds = [Collections.Generic.HashSet[uint32]]::new()
        [void]$descendantIds.Add([uint32]$process.Id)
        do {
            $added = $false
            foreach ($item in $snapshot) {
                if ($descendantIds.Contains([uint32]$item.ParentProcessId)) {
                    if ($descendantIds.Add([uint32]$item.ProcessId)) { $added = $true }
                }
            }
        } while ($added)
        $runtime = $snapshot | Where-Object {
            $_.Name -eq 'msedgewebview2.exe' -and $descendantIds.Contains([uint32]$_.ProcessId)
        } | Select-Object -First 1
        # Only the page's JavaScript requests /api/sessions during this check.
        # This distinguishes a working embedded page from a blank native window.
        $pageRan = (Test-Path -LiteralPath $logPath) -and
            (Select-String -LiteralPath $logPath -Pattern 'GET /api/sessions HTTP/1.1" 200' -Quiet)
        if ($window -and $runtime -and $pageRan) {
            try {
                $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -Headers @{ 'X-RF-Request' = '1' } -TimeoutSec 3
                if ($health.ok -eq $true) {
                    $ready = $true
                    break
                }
            } catch {
                # The UI and HTTP server may finish starting on adjacent polls.
            }
        }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) {
        throw ('Timed out waiting for the EXE desktop UI/API: window={0}, WebView2 child={1}, page JavaScript={2}.' -f [bool]$window, [bool]$runtime, [bool]$pageRan)
    }
    $page = Invoke-WebRequest -Uri "$baseUrl/" -UseBasicParsing -TimeoutSec 10
    if ($page.StatusCode -ne 200 -or $page.Content -notmatch '<title>remote-folder</title>') {
        throw 'The packaged HTML page is missing.'
    }
    foreach ($asset in @('app.js', 'style.css')) {
        $response = Invoke-WebRequest -Uri "$baseUrl/static/$asset" -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -ne 200 -or $response.RawContentLength -eq 0) {
            throw "The packaged static asset is missing: $asset"
        }
    }
    if (-not $window.CloseMainWindow()) {
        throw 'Could not close the desktop window normally.'
    }
    if (-not $process.WaitForExit(30000)) {
        throw 'The EXE did not exit after its window was closed.'
    }
    if ($process.ExitCode -ne 0) {
        throw "The EXE returned exit code $($process.ExitCode)."
    }
    Write-Host 'PASS: standalone EXE starts its desktop UI, uses system WebView2, serves HTML/JS/CSS/API, and exits cleanly.'
} finally {
    if (Test-Path -LiteralPath $logPath) {
        Write-Host 'Application log:'
        Get-Content -LiteralPath $logPath -Tail 60
    }
    if ($null -ne $process) {
        $process.Refresh()
        if (-not $process.HasExited) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F | Out-Null
        }
    }
    foreach ($name in $variables) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
