@echo off
rem Installs a GitHub Actions self-hosted runner as a service. Asks for admin.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-runner.ps1"
