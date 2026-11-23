@echo off
rem Adds Coplay's MCP for Unity to Unity projects. Double-click to pick from your projects,
rem or drag a project folder onto this file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0add-unity-mcp.ps1" %*
