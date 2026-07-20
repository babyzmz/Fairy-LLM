@echo off
setlocal
title Fairy V3 Development
cd /d "%~dp0"

set "FAIRY_LAUNCHER=%~dp0fairy-v3\scripts\start-desktop.ps1"
if not exist "%FAIRY_LAUNCHER%" (
    echo Fairy V3 launcher was not found:
    echo %FAIRY_LAUNCHER%
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%FAIRY_LAUNCHER%" -Toggle %*
set "FAIRY_EXIT_CODE=%ERRORLEVEL%"
if not "%FAIRY_EXIT_CODE%"=="0" (
    echo.
    echo Fairy V3 development launcher failed with exit code %FAIRY_EXIT_CODE%.
    pause
)
exit /b %FAIRY_EXIT_CODE%
