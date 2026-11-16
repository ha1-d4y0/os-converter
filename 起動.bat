@echo off
chcp 65001 >nul
cd /d "%~dp0"
python os_porter.py %*
if errorlevel 1 pause
