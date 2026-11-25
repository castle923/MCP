@echo off
rem hub-desk: starts the server and opens the browser. Started again while running, it only opens the browser.
rem Closing this window stops hub-desk and every agent it started.
set "PYTHONPATH=%~dp0"
"%~dp0.venv\Scripts\python.exe" -m hub_desk %*
if errorlevel 1 pause
