# Polymarket Phase 1 pipeline — Windows Task Scheduler wrapper (本機開發用).
#
# 用法（在 PowerShell 中）：
#   .\scripts\polymarket_pipeline.ps1            # 正常執行
#   $env:POLY_EXTRA_ARGS="--dry-run"; .\scripts\polymarket_pipeline.ps1   # dry run
#
# Task Scheduler 設定：
#   Trigger: 每 5 分鐘
#   Action:  powershell.exe -ExecutionPolicy Bypass -File D:\All-money-back-me-home\scripts\polymarket_pipeline.ps1

$ErrorActionPreference = "Continue"

$ProjectRoot = if ($env:PROJECT_ROOT) { $env:PROJECT_ROOT } else { "D:\All-money-back-me-home" }
$LogDir      = if ($env:LOG_DIR) { $env:LOG_DIR } else { Join-Path $ProjectRoot "logs" }
$LockFile    = if ($env:LOCK_FILE) { $env:LOCK_FILE } else { Join-Path $env:TEMP "polymarket_pipeline.lock" }
$LogFile     = Join-Path $LogDir "polymarket.log"
$StatusFile  = Join-Path $ProjectRoot "data\reports\polymarket_pipeline_status.json"

$MarketsLimit = if ($env:POLY_MARKETS_LIMIT) { $env:POLY_MARKETS_LIMIT } else { "20" }
$WalletsCap   = if ($env:POLY_WALLETS_CAP) { $env:POLY_WALLETS_CAP } else { "30" }
$ExtraArgs    = if ($env:POLY_EXTRA_ARGS) { $env:POLY_EXTRA_ARGS } else { "" }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $StatusFile) | Out-Null

function Log([string]$msg) {
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $LogFile -Value "[$ts] $msg"
}

# 簡易 lockfile（不是真的 flock，只是標記檔存在就跳過）
if (Test-Path $LockFile) {
    $lockAge = (Get-Date) - (Get-Item $LockFile).LastWriteTime
    if ($lockAge.TotalMinutes -lt 10) {
        Log "previous run lock present ($($lockAge.TotalMinutes) min old); skipping"
        exit 0
    }
    Log "stale lock (> 10 min), removing"
    Remove-Item $LockFile -Force
}
New-Item -ItemType File -Path $LockFile -Force | Out-Null

$tsStart = (Get-Date).ToUniversalTime()
$tsStartStr = $tsStart.ToString("yyyy-MM-dd HH:mm:ss")
Log "pipeline start"

Set-Location $ProjectRoot

$args = @("--markets-limit", $MarketsLimit, "--wallets-cap", $WalletsCap)
if ($ExtraArgs) { $args += $ExtraArgs.Split(" ") }

$proc = Start-Process -FilePath "python" -ArgumentList (@("-m","polymarket.pipeline") + $args) `
    -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput "$LogFile.stdout" `
    -RedirectStandardError "$LogFile.stderr"

Get-Content "$LogFile.stdout" -ErrorAction SilentlyContinue | ForEach-Object { Add-Content $LogFile $_ }
Get-Content "$LogFile.stderr" -ErrorAction SilentlyContinue | ForEach-Object { Add-Content $LogFile $_ }
Remove-Item "$LogFile.stdout","$LogFile.stderr" -ErrorAction SilentlyContinue

$tsEnd = (Get-Date).ToUniversalTime()
$tsEndStr = $tsEnd.ToString("yyyy-MM-dd HH:mm:ss")
$duration = [int]($tsEnd - $tsStart).TotalSeconds
$exit = $proc.ExitCode
$result = if ($exit -eq 0) { "ok" } else { "fail" }

Log "pipeline $result (exit=$exit, ${duration}s)"

$status = @{
    last_run_start   = $tsStartStr
    last_run_end     = $tsEndStr
    duration_seconds = $duration
    result           = $result
    exit_code        = $exit
    mode             = "bare-powershell"
    markets_limit    = [int]$MarketsLimit
    wallets_cap      = [int]$WalletsCap
} | ConvertTo-Json

$status | Out-File -FilePath $StatusFile -Encoding utf8

Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
exit $exit
