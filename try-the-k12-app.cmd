@echo off
rem Try the PC's Deck app on this PC: the same window it opens on the deck's screen,
rem talking to pretend hardware. Nothing real is touched. Needs Python 3.
title Deck app demo
where python >nul 2>&1 || (echo Python not found. Install it with:  winget install Python.Python.3.12 & pause & exit /b 1)
rem build the native app (Windows' own C# compiler) into a temp folder; it waits for the demo below
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0k12-windows\app\build-app.ps1" -Out "%TEMP%\deck-demo-app\Deck.exe" >nul
if exist "%TEMP%\deck-demo-app\Deck.exe" (
  start "" "%TEMP%\deck-demo-app\Deck.exe" --pi 127.0.0.1 --port 8090 --at-sign-in
) else (
  echo Couldn't build the native app - opening the browser version instead.
  start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0k12-windows\app\Deck.ps1" -AtSignIn -Pi 127.0.0.1 -Port 8090
)
echo The Deck app window opens in a few seconds. Cause trouble at http://127.0.0.1:8090/demo
python "%~dp0pi5\deck-demo.py" %*
pause
