@echo off
cd /d "%~dp0"
echo Saving the world and stopping Minecraft...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deck-compose.ps1" stop
pause
