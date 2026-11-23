@echo off
rem Adds glTFast (.glb/.gltf model import) to Unity projects. Double-click to pick from your projects,
rem or drag a project folder onto this file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0add-gltfast.ps1" %*
