"""Guide first-time users to sign in through the official Gemini page in Ego Lite."""
import json
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk
from urllib.parse import urlsplit

from ego_installation import ego_binary, ego_cli, ensure_ego_background, preferred_profile


ROOT = Path(__file__).resolve().parent
GEMINI_URL = 'https://gemini.google.com/app'


def check_login(cli):
    ensure_ego_background(cli)
    module = (ROOT / 'login_check.mjs').as_uri()
    script = f"console.log('GEMINI_LOGIN '+JSON.stringify(await (await import('{module}')).checkLogin()))"
    result = subprocess.run([cli, 'nodejs', '-e', script], timeout=55,
                            capture_output=True, encoding='utf8', errors='replace',
                            creationflags=subprocess.CREATE_NO_WINDOW)
    for stream in (result.stdout, result.stderr):
        for line in stream.splitlines():
            if line.startswith('GEMINI_LOGIN '):
                return json.loads(line.removeprefix('GEMINI_LOGIN '))
    return {'status': 'browser_unavailable'}


def has_used_gemini(state):
    for path in (state / 'conversations').glob('*.json'):
        try:
            saved = json.loads(path.read_text(encoding='utf8'))
            url = urlsplit(saved.get('url', ''))
            if url.scheme == 'https' and url.hostname == 'gemini.google.com' and saved.get('history'):
                return True
        except (ValueError, OSError, TypeError, AttributeError):
            continue
    return False


def ensure_login(state, *, force=False):
    marker = state / 'gemini-login-verified'
    if not force and (marker.is_file() or has_used_gemini(state)):
        return True

    root = tk.Tk()
    root.title('Gemini Codex Web')
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24)
    frame.grid()
    ttk.Label(frame, text='登录 Gemini', font=('Segoe UI', 16, 'bold')).grid(sticky='w')
    ttk.Label(frame, text='在 Ego Lite 的 Gemini 网页登录你的 Google 账号。登录后点击「检查并继续」。',
              wraplength=420).grid(sticky='w', pady=(8, 14))
    status = tk.StringVar(value='如果你已经登录，可以直接检查。')
    ttk.Label(frame, textvariable=status, wraplength=420).grid(sticky='w', pady=(0, 14))
    actions = ttk.Frame(frame)
    actions.grid(sticky='e')
    results = queue.Queue()
    active = False
    verified = False
    cancel_requested = False
    selected_profile = preferred_profile()

    def open_login():
        cli = ego_cli()
        binary = ego_binary(cli) if cli else None
        if binary is None:
            status.set('找不到 Ego Lite。请先运行安装包中的 Install.cmd。')
            return
        profile = [f'--profile-directory={selected_profile}'] if selected_profile else []
        try:
            subprocess.Popen([str(binary), *profile, GEMINI_URL])
        except OSError:
            status.set('无法打开 Ego Lite。请打开 Ego Lite 并访问 gemini.google.com。')
        else:
            status.set('请在 Ego Lite 中完成登录及必要的 Google 验证，再返回检查。')

    def check():
        nonlocal active
        cli = ego_cli()
        if not cli:
            status.set('找不到 Ego Lite。请先运行安装包中的 Install.cmd。')
            return
        active = True
        verify_button.state(['disabled'])
        status.set('正在检查 Gemini 网页…')

        def worker():
            try:
                results.put(check_login(cli))
            except (OSError, ValueError, subprocess.SubprocessError):
                results.put({'status': 'browser_unavailable'})

        threading.Thread(target=worker, daemon=True).start()
        root.after(150, collect_result)

    def collect_result():
        nonlocal active, verified, selected_profile
        try:
            result = results.get_nowait()
        except queue.Empty:
            root.after(150, collect_result)
            return
        active = False
        if cancel_requested:
            root.destroy()
            return
        verify_button.state(['!disabled'])
        if result.get('profile'):
            selected_profile = result['profile']
        if result['status'] == 'ready':
            try:
                state.mkdir(parents=True, exist_ok=True)
                marker.touch()
            except OSError:
                status.set('已确认登录，但无法保存本机设置，请检查安装权限。')
                return
            verified = True
            root.destroy()
            return
        messages = {
            'login_required': 'Gemini 尚未登录。请在 Ego Lite 网页登录后重试。',
            'region_unavailable': 'Gemini 网页提示当前地区暂不支持。网页可用后再检查。',
            'page_unavailable': '暂时无法确认登录。请在 Ego Lite 中确认 Gemini 已显示聊天输入框。',
            'browser_unavailable': '无法连接 Ego Lite。请先打开浏览器并完成 Ego Lite 的首次设置。',
        }
        status.set(messages.get(result['status'], messages['page_unavailable']))

    def cancel():
        nonlocal cancel_requested
        if active:
            cancel_requested = True
            status.set('正在完成这次检查，稍后可以关闭。')
        else:
            root.destroy()

    ttk.Button(actions, text='在 Ego Lite 登录', command=open_login).grid(row=0, column=0, padx=(0, 6))
    verify_button = ttk.Button(actions, text='检查并继续', command=check)
    verify_button.grid(row=0, column=1, padx=(0, 6))
    ttk.Button(actions, text='取消', command=cancel).grid(row=0, column=2)
    root.protocol('WM_DELETE_WINDOW', cancel)
    root.mainloop()
    return verified
