@echo off
rem Try the Deck Panel on this PC with pretend hardware. Nothing real is touched.
rem Needs Python 3 (winget install Python.Python.3.12).
title Deck Panel demo
where python >nul 2>&1 || (echo Python not found. Install it with:  winget install Python.Python.3.12 & pause & exit /b 1)
start "" http://127.0.0.1:8090/demo
python "%~dp0pi5\deck-demo.py" %*
pause
