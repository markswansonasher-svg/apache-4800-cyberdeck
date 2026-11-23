@echo off
cd /d "%~dp0"
findstr /r /i "^MC_EULA=TRUE" .env >nul 2>&1
if errorlevel 1 (
  echo.
  echo  The Minecraft EULA isn't accepted yet.
  echo  Open .env in this folder, read the EULA link, and set MC_EULA=TRUE.
  echo.
  notepad .env
  pause
  exit /b 1
)
rem deck-compose adds the active modpack's settings (if any) to .env
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deck-compose.ps1" up -d
if errorlevel 1 (
  echo Docker isn't running? Open Docker Desktop and try again.
  pause
  exit /b 1
)
echo.
echo Minecraft is starting. First start downloads the server - give it a few minutes.
echo Connect to: this PC's IP, or 10.48.0.10 from the laptop, port 25565.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deck-compose.ps1" ps
pause
