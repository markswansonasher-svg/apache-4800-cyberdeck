@echo off
rem Double-click me. Asks for admin, then sets up the PC's case network + wake-up.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-k12-network.ps1"
