@echo off
title EchoSpeak AI
cd /d "%~dp0"

echo ========================================
echo   EchoSpeak AI
echo ========================================
echo.

:: Check Redis
redis-cli ping >nul 2>&1
if errorlevel 1 (
    echo Redis is not running! Start it first: redis-server
    pause
    exit /b 1
)

:: Python Engine
echo Starting Python AI Engine...
start "EchoSpeak-Engine" cmd /k "cd /d %~dp0python-engine && python main.py"
timeout /t 5 /nobreak >nul

:: Go Gateway
echo Starting Go Gateway + Frontend...
start "EchoSpeak-Gateway" cmd /k "cd /d %~dp0go-gateway && gateway.exe"
timeout /t 3 /nobreak >nul

start http://localhost:8080
echo.
echo http://localhost:8080
pause
