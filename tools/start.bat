@echo off
REM ============================================================
REM  ?? Wenshu ? ?????? Docker?
REM ============================================================
setlocal
set "ROOT=X:\????\wenshu"
set "VENV=%ROOT%\.venv"
set "PORT=8000"
if not "%WENSHU_PORT%"=="" set "PORT=%WENSHU_PORT%"

cd /d "%ROOT%\backend"
if errorlevel 1 ( echo [ERROR] ???? backend ?? & exit /b 1 )

set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
REM ????? NO_PROXY????? [::1]??? httpx ????
set NO_PROXY=localhost,127.0.0.1,::1
set no_proxy=localhost,127.0.0.1,::1

echo ============================================
echo   ?? Wenshu
echo   ??: http://127.0.0.1:%PORT%
echo ============================================
"%VENV%\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --log-level info
endlocal
