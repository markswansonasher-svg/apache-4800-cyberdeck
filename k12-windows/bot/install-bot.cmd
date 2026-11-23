@echo off
rem Installs the deck's Discord bot + watchdog on this PC (no Pi needed). Not as admin.
rem   install-bot.cmd -Remove   turns it off again (e.g. once the Pi runs the bot)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-bot.ps1" %*
pause
