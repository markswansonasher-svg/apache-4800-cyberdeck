@echo off
rem Re-exports every STL after you change params.scad. Takes a few minutes.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
pause
