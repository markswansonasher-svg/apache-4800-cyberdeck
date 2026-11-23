@echo off
rem Double-click me (after setup-pi.sh has run on the Pi). Asks for admin.
rem Lets the Pi start/stop Minecraft, read temperatures and switch travel mode.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-k12-agent.ps1" -DeskUser "%USERNAME%"
