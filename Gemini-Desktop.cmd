@echo off
if exist "%~dp0.artifact-venv\Scripts\python.exe" (
    "%~dp0.artifact-venv\Scripts\python.exe" "%~dp0start.py" --desktop
) else (
    python "%~dp0start.py" --desktop
)
if errorlevel 1 pause
