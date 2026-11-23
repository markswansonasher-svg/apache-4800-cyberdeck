@echo off
rem Puts the laptop's Ethernet port back to normal (DHCP). Asks for admin.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-laptop.ps1" -Undo
