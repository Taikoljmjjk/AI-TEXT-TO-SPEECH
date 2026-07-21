@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo Cai dat hoan tat. Hay mo CHAY_TOOL.bat
pause
