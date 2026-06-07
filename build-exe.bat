@echo off
chcp 65001 >nul
title Build EchoSpeak Engine EXE

echo ========================================
echo   Build Python Engine with PyInstaller
echo ========================================
echo.

cd /d "%~dp0python-engine"

:: Check PyInstaller
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

:: Build single exe
echo.
echo Building echospeak-engine.exe...
pyinstaller --onefile --name echospeak-engine --hidden-import=proto.aiservice_pb2 --hidden-import=proto.aiservice_pb2_grpc --hidden-import=services --hidden-import=services.asr_engine --hidden-import=services.llm_engine --hidden-import=services.tts_engine --hidden-import=services.correction_engine --hidden-import=faster_whisper --hidden-import=edge_tts --add-data "proto;proto" --add-data "..\go-gateway\proto\aiservice.proto;." main.py

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo   Build complete!
    echo   dist\echospeak-engine.exe
    echo ========================================
) else (
    echo.
    echo [ERROR] Build failed!
)

pause
