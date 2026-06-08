# YaoCoin Trading System - Windows Watchdog Script
# Hourly health check with auto-recovery
# Fix #4 (Round3): 僵尸端口检测 — 端口被非预期进程占用时强制清理
$ErrorActionPreference = "Continue"
$ProjectDir = "D:\妖币系统\妖币交易系统"
$PythonBin = "C:\Users\liu\.workbuddy\binaries\python\versions\3.13.12\python.exe"
$WebPort = 8081

$HealthOk = $true
$Report = @()
$Report += "========================================"
$Report += "  YaoCoin Health Check - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$Report += "========================================"

# 1. Check process status
$Processes = Get-WmiObject Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -like "*main.py*"
}
$WebProcs = Get-WmiObject Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -like "*web_server.py*"
}

if ($Processes) {
    foreach ($p in $Processes) {
        $Report += "[OK] Main Engine running | PID: $($p.ProcessId) | Mem: $([math]::Round($p.WorkingSetSize/1MB,0))MB"
    }
} else {
    $Report += "[FAIL] Main Engine NOT running!"
    $HealthOk = $false
}

if ($WebProcs) {
    foreach ($p in $WebProcs) {
        $Report += "[OK] Web Server running | PID: $($p.ProcessId) | Mem: $([math]::Round($p.WorkingSetSize/1MB,0))MB"
    }
} else {
    $Report += "[FAIL] Web Server NOT running!"
    $HealthOk = $false
}

# 2. Check port 8081 via TCP connection test
$PortInUse = $false
try {
    $tcpTest = Test-NetConnection -ComputerName "127.0.0.1" -Port $WebPort -WarningAction SilentlyContinue -ErrorAction Stop
    if ($tcpTest.TcpTestSucceeded) {
        $Report += "[OK] Port $WebPort listening"
        $PortInUse = $true
    } else {
        $Report += "[WARN] Port $WebPort connection failed (API check below)"
    }
} catch {
    $Report += "[WARN] Port check skipped (API test will verify connectivity)"
}

# Fix #4 (Round3): 检测僵尸端口 — 端口被占用但 Web Server 进程不存在
if ($PortInUse -and -not $WebProcs) {
    $Report += "[WARN] Port $WebPort occupied but web_server.py NOT running! Possible zombie process."
    # 找到占用端口的进程
    try {
        $portOwner = Get-NetTCPConnection -LocalPort $WebPort -ErrorAction SilentlyContinue | 
                      Select-Object -First 1 -ExpandProperty OwningProcess
        if ($portOwner) {
            $zombieProc = Get-Process -Id $portOwner -ErrorAction SilentlyContinue
            if ($zombieProc) {
                $Report += "[RECOVER] Killing zombie process on port ${WebPort}: PID=$portOwner ($($zombieProc.ProcessName))"
                Stop-Process -Id $portOwner -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 3
                # 验证端口已释放
                $tcpCheck = Test-NetConnection -ComputerName "127.0.0.1" -Port $WebPort -WarningAction SilentlyContinue -ErrorAction SilentlyContinue
                if ($tcpCheck.TcpTestSucceeded) {
                    $Report += "[WARN] Port $WebPort still in use after kill attempt"
                } else {
                    $Report += "[OK] Port $WebPort freed after zombie cleanup"
                }
            }
        } else {
            $Report += "[WARN] Could not identify process on port $WebPort"
        }
    } catch {
        $Report += "[WARN] Zombie port cleanup failed: $_"
    }
}

# 3. Test API endpoints
try {
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:8081/api/status" -TimeoutSec 10
    $Report += "[OK] API /api/status -> mode: $($status.decision_mode) | running: $($status.running)"
} catch {
    $Report += "[FAIL] API /api/status error: $_"
    $HealthOk = $false
}

try {
    $account = Invoke-RestMethod -Uri "http://127.0.0.1:8081/api/account" -TimeoutSec 10
    $equity = [math]::Round($account.total_equity, 2)
    $Report += "[OK] API /api/account -> equity: `$$equity | positions: $($account.position_count)"
} catch {
    $Report += "[FAIL] API /api/account error: $_"
    $HealthOk = $false
}

try {
    $strat = Invoke-RestMethod -Uri "http://127.0.0.1:8081/api/strategy_status" -TimeoutSec 10
    $Report += "[OK] API /api/strategy_status -> action: $($strat.action) | score: $($strat.score)"
} catch {
    $Report += "[FAIL] API /api/strategy_status error: $_"
}

# 4. Check recent log errors
$todayLog = "$ProjectDir\logs\wangcai_$(Get-Date -Format 'yyyy-MM-dd').log"
if (Test-Path $todayLog) {
    $logErrors = Get-Content $todayLog -Tail 200 -Encoding UTF8 | Select-String "ERROR|CRITICAL|Exception|Traceback"
    if ($logErrors) {
        $errCount = ($logErrors | Measure-Object).Count
        $Report += "[WARN] Log has $errCount error(s) in recent entries"
        if ($errCount -gt 5) { $HealthOk = $false }
    } else {
        $Report += "[OK] Log clean - no errors"
    }
} else {
    $Report += "[WARN] Today's log file not found: $todayLog"
}

# 5. System memory
$os = Get-CimInstance Win32_OperatingSystem
$totalMem = $os.TotalVisibleMemorySize / 1KB
$freeMem = $os.FreePhysicalMemory / 1KB
$memUsage = [math]::Round((($totalMem - $freeMem) / $totalMem) * 100, 1)
if ($memUsage -gt 90) {
    $Report += "[WARN] System memory: ${memUsage}% (HIGH)"
} else {
    $Report += "[OK] System memory: ${memUsage}%"
}

# Summary
$Report += "----------------------------------------"
if ($HealthOk) {
    $Report += "[RESULT] HEALTHY - All checks passed"
} else {
    $Report += "[RESULT] UNHEALTHY - Issues detected"
}
$Report += "========================================"

# Output report
$Report | ForEach-Object { Write-Output $_ }

# Auto-recovery on failure
if (-not $HealthOk) {
    Write-Output ""
    Write-Output ">>> Starting auto-recovery..."

    # Fix #4 (Round3): 重启前先确保端口已释放（避免僵尸端口导致启动失败）
    try {
        $preCheck = Test-NetConnection -ComputerName "127.0.0.1" -Port $WebPort -WarningAction SilentlyContinue -ErrorAction SilentlyContinue
        if ($preCheck.TcpTestSucceeded -and -not $WebProcs) {
            Write-Output "[RECOVER] Port $WebPort still occupied by zombie, cleaning up..."
            $zPid = Get-NetTCPConnection -LocalPort $WebPort -ErrorAction SilentlyContinue | 
                     Select-Object -First 1 -ExpandProperty OwningProcess
            if ($zPid) {
                Stop-Process -Id $zPid -Force -ErrorAction SilentlyContinue
                Write-Output "  Killed zombie PID: $zPid"
                Start-Sleep -Seconds 3
            }
        }
    } catch {
        Write-Output "[RECOVER] Pre-restart port cleanup check skipped: $_"
    }

    if (-not $WebProcs) {
        Write-Output "[RECOVER] Starting Web Server..."
        Start-Process -FilePath $PythonBin -ArgumentList "web_server.py" -WorkingDirectory $ProjectDir -WindowStyle Hidden
        Start-Sleep -Seconds 5
    }

    if (-not $Processes) {
        Write-Output "[RECOVER] Starting Main Engine..."
        Start-Process -FilePath $PythonBin -ArgumentList "main.py --config config/system.yaml --mode live --confirm-yes" -WorkingDirectory $ProjectDir -WindowStyle Hidden
        Start-Sleep -Seconds 5
    } else {
        # 引擎进程存在但API不通 → 强制重启
        Write-Output "[RECOVER] Engine process exists but API unreachable -> force restart"
        foreach ($p in $Processes) { Stop-Process -Id $p.ProcessId -Force; Write-Output "  Killed PID $($p.ProcessId)" }
        Start-Sleep -Seconds 3
        # Fix #4 (Round3): 重启前再次确认端口状态
        try {
            $postKill = Test-NetConnection -ComputerName "127.0.0.1" -Port $WebPort -WarningAction SilentlyContinue -ErrorAction SilentlyContinue
            if ($postKill.TcpTestSucceeded) {
                Write-Output "[RECOVER] Port $WebPort still occupied after killing engine, cleaning..."
                $remPid = Get-NetTCPConnection -LocalPort $WebPort -ErrorAction SilentlyContinue | 
                           Select-Object -First 1 -ExpandProperty OwningProcess
                if ($remPid) {
                    Stop-Process -Id $remPid -Force -ErrorAction SilentlyContinue
                    Write-Output "  Killed remaining PID: $remPid"
                    Start-Sleep -Seconds 3
                }
            }
        } catch {}
        Start-Process -FilePath $PythonBin -ArgumentList "main.py --config config/system.yaml --mode live --confirm-yes" -WorkingDirectory $ProjectDir -WindowStyle Hidden
        Write-Output "  Engine restarted"
    }

    Start-Sleep -Seconds 3
    Write-Output "[RECOVER] Rechecking status..."
    try {
        $recheck = Invoke-RestMethod -Uri "http://127.0.0.1:8081/api/status" -TimeoutSec 10
        Write-Output "[RECOVER] Status after recovery: $($recheck | ConvertTo-Json -Compress)"
    } catch {
        Write-Output "[RECOVER] FAILED - manual intervention required!"
    }
}

exit 0
