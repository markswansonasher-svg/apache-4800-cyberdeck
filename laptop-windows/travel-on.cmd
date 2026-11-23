@echo off
rem Laptop gets internet through the deck (turn travel mode on in the Deck Panel first). Asks for admin.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0travel.ps1" -Mode on
