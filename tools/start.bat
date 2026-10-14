@echo off
chcp 65001 >nul
REM ============================================================
REM  文枢 · 前台启动（关掉窗口服务就停）
REM
REM  后台常驻请改用 start-background.bat
REM ============================================================
setlocal
set "ROOT=%~dp0.."
set "VENV=%ROOT%\.venv"
set "PORT=8000"
if not "%WENSHU_PORT%"=="" set "PORT=%WENSHU_PORT%"

if not exist "%VENV%\Scripts\python.exe" (
  echo [错误] 找不到虚拟环境：%VENV%
  echo         请确认项目目录完整。
  pause
  exit /b 1
)

cd /d "%ROOT%\backend" || (echo [错误] 无法进入 backend 目录 & pause & exit /b 1)

set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
REM 必须显式覆盖 NO_PROXY：系统值含 "[::1]" 这种写法，
REM httpx 解析时会当成 host:port，报 InvalidURL。
set NO_PROXY=localhost,127.0.0.1,::1
set no_proxy=localhost,127.0.0.1,::1

echo ============================================
echo   文枢 Wenshu
echo   地址：http://127.0.0.1:%PORT%
echo ============================================
"%VENV%\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --log-level info
endlocal
