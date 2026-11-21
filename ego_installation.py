"""Locate the CLI from the desktop Ego installation, even without a PATH entry."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import winreg


def ego_locations():
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\ego.exe') as key:
                value, _ = winreg.QueryValueEx(key, None)
            yield Path(os.path.expandvars(value.strip('"'))).parent
        except OSError:
            continue
    local = Path(os.environ['LOCALAPPDATA'])
    yield Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Citro Labs' / 'ego lite' / 'Application'
    yield local / 'Citro Labs' / 'ego lite' / 'Application'
    yield local / 'Programs' / 'Citro Labs' / 'ego lite' / 'Application'


def ego_cli():
    for application in ego_locations():
        installed = []
        for exe in application.glob('*/ego-browser.exe'):
            match = re.match(r'^\d+(?:\.\d+)*', exe.parent.name)
            if match and exe.is_file():
                installed.append((tuple(map(int, match[0].split('.'))), exe))
        if installed:
            return str(max(installed, key=lambda item: item[0])[1])
        direct = application / 'ego-browser.exe'
        if direct.is_file():
            return str(direct)
    exe = shutil.which('ego-browser')
    if exe:
        return exe
    legacy = Path.home() / '.local/share/ego/active_version_dir/ego-browser.exe'
    return str(legacy) if legacy.is_file() else None


def ego_binary(cli):
    folder = Path(cli).parent
    return next((candidate for candidate in (folder.parent / 'ego.exe', folder / 'ego.exe')
                 if candidate.is_file()), None)


def preferred_profile():
    local = Path(os.environ['LOCALAPPDATA']) / 'Citro Labs'
    if any((local / app / 'User Data' / 'Profile 1').is_dir() for app in ('ego lite', 'ego')):
        return 'Profile 1'
    return None


def ensure_ego_background(cli):
    """Start the installed profile quietly before the CLI tries to open Ego."""
    result = subprocess.run(
        ['tasklist.exe', '/FI', 'IMAGENAME eq ego.exe', '/FO', 'CSV', '/NH'],
        capture_output=True, text=True, errors='replace', check=True, timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if any(line.casefold().startswith('"ego.exe",') for line in result.stdout.splitlines()):
        return

    binary = ego_binary(cli)
    if binary is None:
        raise FileNotFoundError('ego.exe')
    picked = preferred_profile()
    profile = [f'--profile-directory={picked}'] if picked else []
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 7  # SW_SHOWMINNOACTIVE
    process = subprocess.Popen(
        [str(binary), *profile, '--startup-ego-browser-service', '--start-minimized'],
        startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        return
    if process.returncode:
        raise OSError(f'Ego browser could not start ({process.returncode})')
