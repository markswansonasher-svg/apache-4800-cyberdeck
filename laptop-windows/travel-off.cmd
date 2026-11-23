@echo off
rem Back to the laptop's own Wi-Fi. Asks for admin.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0travel.ps1" -Mode off
