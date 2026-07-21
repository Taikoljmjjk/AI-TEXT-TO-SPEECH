@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0TAO_FILE_PHAN_PHOI.ps1"
if errorlevel 1 (
    echo.
    echo Tao file phan phoi that bai.
    pause
)

