@echo off
if exist "%~dp0.artifact-venv\Scripts\python.exe" (
    "%~dp0.artifact-venv\Scripts\python.exe" "%~dp0start.py" %*
) else (
    python "%~dp0start.py" %*
)
if errorlevel 1 pause
