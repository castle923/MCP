@echo off
rem hub-desk installer: double-click it in the unzipped folder. Details: INSTALL.md
rem -ExecutionPolicy Bypass applies to this one PowerShell process only; the system setting is not changed.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
