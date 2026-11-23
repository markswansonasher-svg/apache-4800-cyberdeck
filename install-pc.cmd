@echo off
rem Sets up the cyberdeck's mini PC in one go: checks, apps, Docker, network, Minecraft, link to the Pi.
rem Copy the whole cyberdeck folder to the PC (or plug in the USB drive) and double-click me.
rem Do NOT "Run as administrator" - it asks for admin itself when it needs it.
rem Safe to run again: it skips what's done and carries on where it stopped.
rem   install-pc.cmd -Check   only looks, changes nothing
rem   install-pc.cmd -Redo    goes through every step again
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0k12-windows\setup-pc.ps1" %*
