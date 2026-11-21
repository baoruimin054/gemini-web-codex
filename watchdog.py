"""Keep the local gateway available while the Gemini desktop is running."""
import ctypes
from ctypes import wintypes
import errno
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import gateway
from ego_installation import ego_cli


ROOT = Path(__file__).resolve().parent
STATE = gateway.STATE
KERNEL = ctypes.WinDLL('kernel32', use_last_error=True)
KERNEL.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
KERNEL.CreateMutexW.restype = wintypes.HANDLE
KERNEL.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
KERNEL.OpenProcess.restype = wintypes.HANDLE
KERNEL.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
KERNEL.WaitForSingleObject.restype = wintypes.DWORD
KERNEL.CloseHandle.argtypes = [wintypes.HANDLE]
KERNEL.CloseHandle.restype = wintypes.BOOL
USER = ctypes.WinDLL('user32', use_last_error=True)
ENUM_CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
USER.EnumWindows.argtypes = [ENUM_CALLBACK, wintypes.LPARAM]
USER.EnumWindows.restype = wintypes.BOOL
USER.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER.IsWindowVisible.argtypes = [wintypes.HWND]
USER.IsWindowVisible.restype = wintypes.BOOL
USER.GetWindowTextLengthW.argtypes = [wintypes.HWND]
USER.GetForegroundWindow.restype = wintypes.HWND
USER.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
LOG = logging.getLogger('gemini_gateway_watchdog')


def find_desktop_pid():
    """Find the main process for the dedicated desktop profile after a restart."""
    command = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        "Get-CimInstance Win32_Process -Filter \"Name='ChatGPT.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, encoding='utf8', errors='replace', timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW, check=True,
        )
        processes = json.loads(result.stdout or '[]')
    except (OSError, ValueError, subprocess.SubprocessError):
        LOG.exception('Could not find the dedicated desktop process')
        return None
    if isinstance(processes, dict):
        processes = [processes]
    expected = os.path.normcase(os.path.normpath(str(STATE / 'desktop')))
    matches = []
    for process in processes:
        command = process.get('CommandLine') or ''
        if re.search(r'(?:^|\s)--type=', command, re.I):
            continue
        match = re.search(r'(?:^|\s)--user-data-dir=(?:"([^"]+)"|(\S+))', command, re.I)
        if match and os.path.normcase(os.path.normpath(match.group(1) or match.group(2))) == expected:
            matches.append(int(process['ProcessId']))
    return matches[0] if len(matches) == 1 else None


def start_gateway(pid):
    subprocess.Popen(
        [sys.executable, str(ROOT / 'start.py'), '--attach-desktop', str(pid)],
        cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def gateway_port_free(port=gateway.PORT):
    try:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(('127.0.0.1', port))
    except OSError as error:
        if error.errno not in (errno.EADDRINUSE, errno.EACCES, 10048, 10013):
            LOG.warning('Gateway port check failed: %s', error)
        return False
    return True


def ensure_gateway(pid, port=gateway.PORT):
    """Start a gateway only when the loopback listener has disappeared."""
    if not gateway_port_free(port):
        return False
    LOG.warning('Gateway listener is down, starting recovery for desktop %s', pid)
    start_gateway(pid)
    return True


def desktop_window_visible(pid):
    windows = []

    @ENUM_CALLBACK
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        USER.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and USER.IsWindowVisible(hwnd) and USER.GetWindowTextLengthW(hwnd):
            name = ctypes.create_unicode_buffer(128)
            USER.GetClassNameW(hwnd, name, len(name))
            if name.value.startswith('Chrome_WidgetWin'):
                windows.append(hwnd)
        return True

    if not USER.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(windows)


def stop_gateway():
    if not gateway_port_free():
        try:
            key = (STATE / 'gateway.key').read_text(encoding='ascii')
            request = urllib.request.Request(
                f'http://127.0.0.1:{gateway.PORT}/_control/shutdown', data=b'',
                headers={'Authorization': 'Bearer ' + key}, method='POST',
            )
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
                if response.status != 200:
                    return False
        except (OSError, urllib.error.URLError):
            LOG.exception('Could not ask the gateway to stop')
            if not gateway_port_free():
                return False
    for _ in range(30):
        if gateway_port_free() and not any(STATE.glob('tmp*/job.json')):
            return True
        time.sleep(0.5)
    LOG.warning('Gateway is still stopping')
    return False


def ego_service_count():
    command = ("(Get-CimInstance Win32_Process -Filter \"Name='ego.exe'\" | "
               "Where-Object { $_.CommandLine -notmatch '(?:^|\\s)--type=' } | "
               "Measure-Object).Count")
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, encoding='utf8', errors='replace', timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        LOG.exception('Could not count Ego browser services')
        return None


def cleanup_call(browser, script):
    result = subprocess.run([browser, 'nodejs', '-e', script], cwd=ROOT,
                            capture_output=True, encoding='utf8', errors='replace',
                            timeout=60, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    for output in (result.stdout, result.stderr):
        for line in output.splitlines():
            if line.startswith('CODEX_CLEANUP_RESULT '):
                return json.loads(line[len('CODEX_CLEANUP_RESULT '):])
    raise ValueError('Browser did not return a cleanup result')


def clear_absent_bindings(seen):
    for file in (STATE / 'conversations').glob('*.json'):
        if file.name in seen:
            continue
        try:
            data = json.loads(file.read_text(encoding='utf8'))
            if isinstance(data.get('space'), int) and data.get('name', '').startswith('Codex Gemini '):
                data.pop('space', None)
                data.pop('targetId', None)
                temporary = file.with_suffix('.cleanup')
                temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
                os.replace(temporary, file)
        except (OSError, ValueError):
            LOG.exception('Could not update a closed browser binding')
            return False
    return True


def close_pending_probes(browser, probes, probes_file):
    for _ in range(min(24, max(8, len(probes) * 8))):
        if not probes:
            break
        script = (f"const r=await (await import('{(ROOT / 'close_spaces.mjs').as_uri()}'))"
                  f".closeCleanupProbes({json.dumps(probes)});"
                  "console.log('CODEX_CLEANUP_RESULT '+JSON.stringify(r))")
        try:
            closed = cleanup_call(browser, script)
            probes = [probe for probe in probes if probe['name'] not in closed]
            if probes:
                probes_file.write_text(json.dumps(probes), encoding='utf8')
            else:
                probes_file.unlink(missing_ok=True)
        except (OSError, ValueError, subprocess.SubprocessError):
            LOG.exception('Could not close a browser cleanup probe')
            break
    return probes


def close_codex_spaces():
    browser = ego_cli()
    if not browser:
        LOG.warning('Could not find ego-browser for space cleanup')
        return False
    count = ego_service_count()
    if count is None:
        return False
    if count == 0:
        return True
    probes_file = STATE / 'cleanup-probes.json'
    try:
        probes = json.loads(probes_file.read_text(encoding='utf8')) if probes_file.exists() else []
    except (OSError, ValueError):
        LOG.exception('Could not read pending cleanup probes')
        return False
    if probes:
        probes = close_pending_probes(browser, probes, probes_file)
        if probes:
            return False
    seen = set()
    visited = set()
    deferred = False
    complete = False
    try:
        for _ in range(min(24, max(8, count * 8))):
            title = ctypes.create_unicode_buffer(512)
            USER.GetWindowTextW(USER.GetForegroundWindow(), title, len(title))
            name = 'Codex Gateway cleanup ' + secrets.token_hex(12)
            pending = {'id': None, 'name': name}
            probes.append(pending)
            probes_file.write_text(json.dumps(probes), encoding='utf8')
            script = (f"const r=await (await import('{(ROOT / 'close_spaces.mjs').as_uri()}'))"
                      f".cleanupPass('{STATE.as_uri()}',{json.dumps(probes)},{json.dumps(name)},"
                      f"{json.dumps(title.value)});console.log('CODEX_CLEANUP_RESULT '+JSON.stringify(r))")
            result = cleanup_call(browser, script)
            probe = result['probe']
            visited.add(probe['name'])
            seen.update(result['seen'])
            deferred = deferred or bool(result['deferred'])
            probes = [item for item in probes if item['name'] != name]
            if probe not in probes:
                probes.append(probe)
            probes_file.write_text(json.dumps(probes), encoding='utf8')
            if len(visited) >= count:
                complete = ego_service_count() == count
                break
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        LOG.exception('Browser space cleanup failed')
    finally:
        probes = close_pending_probes(browser, probes, probes_file)
    if probes or not complete or deferred:
        LOG.warning('Browser space cleanup remains incomplete')
        return False
    if clear_absent_bindings(seen):
        LOG.info('Closed Gemini browser spaces across %d Ego service(s)', count)
        return True
    return False


def close_after_desktop(pid):
    if desktop_window_visible(pid):
        return False
    LOG.info('Gemini desktop closed; stopping gateway and closing browser spaces')
    if not stop_gateway():
        return False
    if desktop_window_visible(pid):
        return False
    return close_codex_spaces()


def monitor(pid):
    mutex = KERNEL.CreateMutexW(None, False, 'Local\\GeminiWebGatewayWatchdog')
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        KERNEL.CloseHandle(mutex)
        return
    try:
        last_start = 0.0
        window_seen = False
        hidden_since = None
        cleanup_done = False
        last_cleanup_attempt = 0.0
        while pid:
            process = KERNEL.OpenProcess(0x00100000, False, pid)
            if process:
                try:
                    while KERNEL.WaitForSingleObject(process, 0) == 258:
                        now = time.monotonic()
                        if desktop_window_visible(pid):
                            if not window_seen:
                                LOG.info('Monitoring Gemini desktop window for PID %s', pid)
                            window_seen = True
                            hidden_since = None
                            if cleanup_done:
                                last_start = 0.0
                                cleanup_done = False
                        elif window_seen:
                            hidden_since = hidden_since or now
                            if now - hidden_since >= 3 and not cleanup_done and now - last_cleanup_attempt >= 15:
                                last_cleanup_attempt = now
                                cleanup_done = close_after_desktop(pid)
                        if window_seen and hidden_since is None and now - last_start >= 15:
                            try:
                                if ensure_gateway(pid):
                                    last_start = time.monotonic()
                            except OSError:
                                last_start = time.monotonic()
                                LOG.exception('Could not restart the gateway')
                        if KERNEL.WaitForSingleObject(process, 1000) != 258:
                            break
                finally:
                    KERNEL.CloseHandle(process)
            # Electron may replace its main process or forward to an existing one.
            previous_pid = pid
            pid = None
            for _ in range(4):
                pid = find_desktop_pid()
                if pid:
                    break
                time.sleep(1)
            if not pid and not cleanup_done:
                close_after_desktop(previous_pid)
            if pid and pid != previous_pid:
                window_seen = False
                hidden_since = None
                cleanup_done = False
    finally:
        KERNEL.CloseHandle(mutex)


if __name__ == '__main__':
    STATE.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(STATE / 'gateway-watchdog.log', maxBytes=262144, backupCount=1,
                                  encoding='utf8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    monitor(int(sys.argv[1]))
