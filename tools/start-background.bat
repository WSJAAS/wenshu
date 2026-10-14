@echo off
chcp 65001 >nul
REM ============================================================
REM  文枢 · 后台常驻启动（不受终端关闭影响）
REM
REM  用法：双击本文件，或在终端运行。
REM
REM  与 start.bat 的区别：
REM    start.bat  —— 前台运行，关掉窗口服务就停
REM    本文件     —— 后台常驻，关掉终端服务继续跑
REM
REM  停止：运行 stop-background.bat，或结束该 python 进程
REM ============================================================
setlocal

set "ROOT=%~dp0.."
set "VENV=%ROOT%\.venv"
set "PORT=8010"
if not "%WENSHU_PORT%"=="" set "PORT=%WENSHU_PORT%"

if not exist "%VENV%\Scripts\python.exe" (
  echo [错误] 找不到虚拟环境：%VENV%
  echo         请确认项目目录完整。
  pause
  exit /b 1
)

cd /d "%ROOT%\backend" || (echo [错误] 无法进入 backend 目录 & pause & exit /b 1)

echo ============================================
echo   文枢 · 后台启动
echo   地址：http://127.0.0.1:%PORT%
echo   日志：%ROOT%\runtime\logs\wenshu.log
echo ============================================
echo.

REM 已经在跑？先提示，避免重复起
netstat -ano | findstr ":%PORT% " | findstr LISTENING >nul 2>&1
if not errorlevel 1 (
  echo [提示] 端口 %PORT% 已在监听，服务可能已在运行。
  echo         如要重启，请先运行 stop-background.bat
  echo.
  pause
  exit /b 0
)

REM 关键：用 Start-Process 脱离当前终端。
REM 这样关掉终端窗口、或上层会话结束，服务都不受影响。
powershell -NoProfile -Command ^
  "$env:PYTHONUNBUFFERED='1'; $env:PYTHONIOENCODING='utf-8'; $env:NO_PROXY='localhost,127.0.0.1,::1'; $env:no_proxy='localhost,127.0.0.1,::1'; Start-Process -FilePath '%VENV%\Scripts\python.exe' -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','%PORT%','--log-level','warning' -WorkingDirectory '%ROOT%\backend' -WindowStyle Hidden"

if errorlevel 1 (
  echo [错误] 启动失败。
  pause
  exit /b 1
)

echo 已在后台启动。等待就绪...
powershell -NoProfile -Command ^
  "for($i=0;$i -lt 40;$i++){ try{ $r=Invoke-WebRequest 'http://127.0.0.1:%PORT%/health' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){ Write-Host '  [就绪] 健康检查通过'; exit 0 } }catch{}; Start-Sleep -Milliseconds 1500 }; Write-Host '  [警告] 60 秒内未就绪，请查看日志'; exit 1"

echo.
echo 打开界面：http://127.0.0.1:%PORT%
echo 停止服务：stop-background.bat
echo.
pause
endlocal
