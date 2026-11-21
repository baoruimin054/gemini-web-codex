"""Locate the CLI from the desktop Ego installation, even without a PATH entry."""
import os
from pathlib import Path
import shutil
import subprocess


def ego_cli():
    application = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Citro Labs' / 'ego lite' / 'Application'
    versions = [p for p in application.glob('*') if all(part.isdecimal() for part in p.name.split('.'))]
    for version in sorted(versions, key=lambda p: tuple(map(int, p.name.split('.'))), reverse=True):
        exe = version / 'ego-browser.exe'
        if exe.is_file():
            return str(exe)
    exe = shutil.which('ego-browser')
    if exe:
        return exe
    legacy = Path.home() / '.local/share/ego/active_version_dir/ego-browser.exe'
    return str(legacy) if legacy.is_file() else None


def ensure_ego_background(cli):
    """Start the installed profile quietly before the CLI tries to open Ego."""
    result = subprocess.run(
        ['tasklist.exe', '/FI', 'IMAGENAME eq ego.exe', '/FO', 'CSV', '/NH'],
        capture_output=True, text=True, errors='replace', check=True, timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if any(line.casefold().startswith('"ego.exe",') for line in result.stdout.splitlines()):
        return

    folder = Path(cli).parent
    binary = next((candidate for candidate in (folder.parent / 'ego.exe', folder / 'ego.exe')
                   if candidate.is_file()), None)
    if binary is None:
        raise FileNotFoundError('ego.exe')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 7  # SW_SHOWMINNOACTIVE
    process = subprocess.Popen(
        [str(binary), '--profile-directory=Profile 1', '--startup-ego-browser-service', '--start-minimized'],
        startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        return
    if process.returncode:
        raise OSError(f'Ego browser could not start ({process.returncode})')
