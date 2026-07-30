param([int]$Port = 8774)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\..")).Path
$url = "http://127.0.0.1:$Port/"
$logPath = Join-Path $env:TEMP "HalloTickets-launch.log"

function Write-LaunchLog([string]$Message) {
    "$(Get-Date -Format s) $Message" | Add-Content -Path $logPath -Encoding UTF8
}

function Find-AppBrowser() {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

function Open-HalloTicketsApp() {
    $browser = Find-AppBrowser
    if ($browser) {
        Write-LaunchLog "Opening standalone app window with $browser"
        Start-Process -FilePath $browser -ArgumentList @("--app=$url", "--start-maximized")
        return
    }
    Write-LaunchLog "No app-mode browser found; using the default browser"
    Start-Process -FilePath $url
}

try {
    Write-LaunchLog "Launching HalloTickets at $url"
    try {
        Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1 | Out-Null
        Write-LaunchLog "Existing local dashboard is ready"
        Open-HalloTicketsApp
        exit 0
    } catch {
        Write-LaunchLog "No server is running yet; starting a new one"
    }

    $poetry = Get-Command poetry -ErrorAction Stop
    Start-Process -FilePath $poetry.Source -ArgumentList @("run", "python", "-m", "mobile.status_dashboard", "--port", "$Port") -WorkingDirectory $repoRoot -WindowStyle Hidden

    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 300
        try {
            Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1 | Out-Null
            Write-LaunchLog "New local dashboard is ready"
            Open-HalloTicketsApp
            exit 0
        } catch {
            # The server is still starting.
        }
    }

    throw "HalloTickets did not start at $url. Check Poetry and project dependencies."
} catch {
    $detail = $_.Exception.Message
    Write-LaunchLog "Launch failed: $detail"
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "HalloTickets could not open.`n$detail`n`nLaunch log: $logPath",
        "HalloTickets",
        "OK",
        "Error"
    ) | Out-Null
    exit 1
}
