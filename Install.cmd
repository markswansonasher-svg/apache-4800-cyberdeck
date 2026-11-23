@echo off
rem Apache 4800 cyberdeck - double-click to set up the deck (PC, Pi, laptop) step by step.
rem Don't "Run as administrator": steps ask for admin themselves when they need it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Install.ps1" %*
