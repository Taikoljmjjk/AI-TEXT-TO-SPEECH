@echo off
setlocal
cd /d "%~dp0"
title TOOL CLONE GIONG TAI LE MMO
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0CHAY_TOOL_CLONE_GIONG.ps1"
if errorlevel 1 pause
endlocal
