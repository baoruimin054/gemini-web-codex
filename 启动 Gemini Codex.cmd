@echo off
setlocal
set "runtime=%~dp0.artifact-venv\Scripts\pythonw.exe"
set "stamp=%~dp0.artifact-venv\gemini-codex-installed"
if not exist "%runtime%" goto install
if exist "%stamp%" goto launch

:install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1"
if errorlevel 1 (
    echo Installation did not complete. See the error above.
    pause
    exit /b 1
)
if not exist "%runtime%" exit /b 1

:launch
start "" "%runtime%" "%~dp0desktop_launcher.pyw"
exit /b %errorlevel%
