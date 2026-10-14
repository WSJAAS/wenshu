@echo off
chcp 65001 >nul
REM ============================================================
REM  文枢 · 停止后台服务
REM
REM  只结束本项目的 python 进程，不影响其它程序。
REM  通过「命令行包含 wenshu\backend」来精确匹配。
REM ============================================================
setlocal
set "ROOT=%~dp0.."
set "PORT=8010"
if not "%WENSHU_PORT%"=="" set "PORT=%WENSHU_PORT%"

echo ============================================
echo   文枢 · 停止服务
echo ============================================
echo.

powershell -NoProfile -Command ^
  "$procs = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*wenshu\\backend*' -or $_.CommandLine -like '*app.main:app*' }; if(-not $procs){ Write-Host '  没有找到运行中的文枢服务。'; exit 0 }; foreach($p in $procs){ Write-Host ('  结束进程 PID=' + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Seconds 2; Write-Host '  已停止。'"

echo.
echo 提示：运行 start-background.bat 可重新启动。
echo.
pause
endlocal
