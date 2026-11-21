"""Start an interactive Codex CLI using the Gemini Web catalog in an isolated home."""
import os
import secrets
import shutil
import subprocess
import sys
import threading
import json
import urllib.request
from pathlib import Path
import gateway
from web_profile import profile

def launch_desktop(stop_event=None):
    # Explorer shortcuts do not inherit the CLI PATH of a Codex shell.
    query = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
             "$pkg=Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1; "
             "$app=(Get-AppxPackageManifest $pkg).Package.Applications.Application | Where-Object Id -eq 'App'; "
             "Join-Path $pkg.InstallLocation $app.Executable")
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',query],
                            capture_output=True,encoding='utf8',errors='replace',timeout=20)
    desktop = Path(result.stdout.strip())
    if result.returncode or not desktop.is_file():
        raise SystemExit('找不到 Windows 已注册的 Codex 桌面程序。请检查 Codex 是否已安装。')
    data = gateway.STATE/'desktop'
    env = dict(os.environ,CODEX_HOME=str(gateway.STATE/'client'),CODEX_ELECTRON_USER_DATA_PATH=str(data))
    app=subprocess.Popen([str(desktop),'--user-data-dir='+str(data)],env=env)
    subprocess.Popen([sys.executable,str(Path(__file__).with_name('quota_panel.py')),str(app.pid)],
                     env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    # Electron can keep its main process alive after the launcher exits.
    subprocess.Popen([sys.executable,str(Path(__file__).with_name('watchdog.py')),str(app.pid)],
                     env=env,creationflags=subprocess.CREATE_NO_WINDOW,
                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if stop_event is None:
        return app.wait()
    while app.poll() is None:
        if stop_event.wait(0.5):
            return 0
    return app.returncode


def wait_for_desktop(pid, stop_event):
    waiter = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive',
                               '-Command', f'Wait-Process -Id {pid}'],
                              creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        while waiter.poll() is None:
            if stop_event.wait(0.5):
                return 0
        return waiter.returncode
    finally:
        if waiter.poll() is None:
            waiter.terminate()
            waiter.wait(timeout=5)

def main():
    home=gateway.STATE/'client'
    home.mkdir(parents=True,exist_ok=True)
    cfg=home/'config.toml'
    if not cfg.exists():
        cfg.write_text(profile(),encoding='utf8')
    try:
        listener=gateway.ThreadingHTTPServer(('127.0.0.1',gateway.PORT),gateway.Handler)
    except OSError as exc:
        if exc.winerror != 10048 or sys.argv[1:] != ['--desktop']:
            raise
        key=(gateway.STATE/'gateway.key').read_text()
        request=urllib.request.Request(f'http://127.0.0.1:{gateway.PORT}/codex/models',headers={'Authorization':'Bearer '+key})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=3) as response:
            if json.load(response) != gateway.catalog():
                raise SystemExit('本机端口已被其他服务占用。')
        return launch_desktop()
    with listener as server:
        credentials=gateway.STATE/'gateway.key'
        if not credentials.exists():
            credentials.write_text(secrets.token_urlsafe(36),encoding='ascii')
        server.key=credentials.read_text()
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            if sys.argv[1:2]==['--attach-desktop']:
                # Keep serving the already-open dedicated window until it exits.
                pid=int(sys.argv[2])
                return wait_for_desktop(pid,server.stopping)
            cli=shutil.which('codex.exe') or 'codex'
            env=dict(os.environ,CODEX_HOME=str(home))
            if sys.argv[1:]==['--desktop']:
                return launch_desktop(server.stopping)
            return subprocess.call([cli,*sys.argv[1:]],env=env)
        finally:
            server.shutdown()

if __name__=='__main__':
    sys.exit(main())
