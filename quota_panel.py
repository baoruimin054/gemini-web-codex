"""A non-activating quota panel owned by the dedicated Gemini desktop window."""
import ctypes as ct
from ctypes import wintypes as wt
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk
from ego_installation import ego_cli, ensure_ego_background

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ['LOCALAPPDATA']) / 'CodexGeminiWeb'
CACHE = STATE / 'quota.json'
USER = ct.windll.user32
KERNEL = ct.windll.kernel32
USER.GetParent.argtypes = [wt.HWND]
USER.GetParent.restype = wt.HWND
USER.SetWindowLongPtrW.argtypes = [wt.HWND, ct.c_int, ct.c_ssize_t]
USER.SetWindowLongPtrW.restype = ct.c_ssize_t
USER.GetWindowLongPtrW.argtypes = [wt.HWND, ct.c_int]
USER.GetWindowLongPtrW.restype = ct.c_ssize_t
KERNEL.CreateMutexW.restype = wt.HANDLE


def find_window(pid):
    handles = []
    callback = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    @callback
    def visit(hwnd, unused):
        owner_pid = wt.DWORD()
        USER.GetWindowThreadProcessId(hwnd, ct.byref(owner_pid))
        if owner_pid.value == pid and USER.IsWindowVisible(hwnd):
            name = ct.create_unicode_buffer(128)
            USER.GetClassNameW(hwnd, name, len(name))
            if name.value.startswith('Chrome_WidgetWin') and USER.GetWindowTextLengthW(hwnd):
                handles.append(hwnd)
        return True
    USER.EnumWindows(visit, 0)
    return handles[0] if handles else None


def reset_text(raw):
    match = re.fullmatch(r'Resets (?:(\w+) (\d{1,2}) )?at (\d{1,2}):(\d{2}) ([AP]M)', raw, re.I)
    if not match:
        return raw or '网页暂未显示重置时间'
    month, day, hour, minute, period = match.groups()
    date = ''
    if month:
        months = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()
        if month[:3].title() not in months:
            return raw
        date = f'{months.index(month[:3].title()) + 1}月{int(day)}日 '
    hours = int(hour) % 12 + (12 if period.upper() == 'PM' else 0)
    return f'{date}{hours:02}:{minute} 重置'


def monitor(pid):
    # One panel per desktop process, including repeated shortcut launches.
    mutex = KERNEL.CreateMutexW(None, False, f'Local\\GeminiWebQuota-{pid}')
    if not mutex or KERNEL.GetLastError() == 183:
        return
    process = KERNEL.OpenProcess(0x100000, False, pid)
    if not process:
        return
    ct.windll.shcore.SetProcessDpiAwareness(2)
    root = tk.Tk()
    root.withdraw()
    root.title('Gemini 额度')
    root.overrideredirect(True)
    # Keep the compact button anchored to the rail even while its card is open.
    clear = '#f0f3f9'
    root.configure(bg=clear)
    canvas = tk.Canvas(root, bg=clear, bd=0, highlightthickness=0)
    canvas.pack(fill='both', expand=True)
    expanded = False
    worker = None
    last_attempt = 0
    started = 0
    error = False
    data = {}
    scale = 1
    attached = None
    hover = None
    width, height = 30, 30
    mask_size = None
    card_x, card_w, card_h = 38, 262, 166

    def draw():
        canvas.delete('all')
        def xy(*coords):
            return [round(v * scale) for v in coords]
        def text(x,y,value,color='#303030',size=14,anchor='w'):
            canvas.create_text(*xy(x,y),text=value,fill=color,anchor=anchor,
                               font=('Segoe UI',-max(9,round(size*scale))))
        def rounded(x,y,w,h,r,fill,outline=''):
            pts=[x+r,y,x+w-r,y,x+w,y,x+w,y+r,x+w,y+h-r,x+w,y+h,x+w-r,y+h,
                 x+r,y+h,x,y+h,x,y+h-r,x,y+r,x,y]
            canvas.create_polygon(*xy(*pts),smooth=True,splinesteps=18,fill=fill,outline=outline)
        if expanded:
            rounded(card_x+1,1,card_w-2,card_h-2,13,'#ffffff','#e1e1e1')
            left,right=card_x+14,card_x+card_w-15
            text(left,19,'额度来源：Gemini 网页','#747474',12)
            canvas.create_line(*xy(left,35,right,35),fill='#ececec')
            for idx,(key,title) in enumerate((('five','5 小时用量上限'),('week','每周限额'))):
                y=41+idx*43
                entry=data.get(key,{})
                text(left,y+10,title)
                remaining=entry.get('remaining')
                text(right,y+10,f'剩余 {remaining}%' if remaining is not None else '待检测',anchor='e')
                text(left,y+29,reset_text(entry.get('reset','')),'#686868',12)
            checked=data.get('checked')
            status=datetime.fromtimestamp(checked/1000).strftime('%H:%M 更新') if checked else '尚未读取'
            canvas.create_line(*xy(left,130,right,130),fill='#ececec')
            text(left,148,'读取中…' if worker else ('刷新失败' if error else status),'#707070',12)
            text(right-27,148,'刷新','#444444',13,'e')
            text(right-1,148,'×','#666666',17,'center')
        # Supersampling keeps the thin ring and diamond smooth at fractional DPI.
        unit=12
        icon=Image.new('RGB',(30*unit,30*unit),clear)
        pen=ImageDraw.Draw(icon)
        def bounds(*values):
            return tuple(round(v*unit) for v in values)
        if expanded or hover=='icon':
            pen.ellipse(bounds(2,2,28,28),fill='#e5e9ef')
        arc=bounds(6,6,24,24)
        pen.ellipse(arc,outline='#cbd0d7',width=16)
        remaining=data.get('five',{}).get('remaining')
        if isinstance(remaining,(int,float)) and remaining>0:
            pen.arc(arc,-90,-90+3.6*min(100,remaining),fill='#565e69',width=21)
        pen.polygon(bounds(15,11.5,18.5,15,15,18.5,11.5,15),fill='#9097a1')
        size=round(30*scale)
        canvas.icon=ImageTk.PhotoImage(icon.resize((size,size),Image.Resampling.LANCZOS))
        canvas.create_image(*xy(0,height-30),image=canvas.icon,anchor='nw')

    def update_view():
        nonlocal data
        try:
            data=json.loads(CACHE.read_text(encoding='utf8'))
        except (OSError,ValueError):
            data={}
        draw()

    def fetch():
        nonlocal worker,last_attempt,started,error
        if worker:
            return
        last_attempt=started=time.monotonic()
        exe=ego_cli()
        script='await (await import('+json.dumps((ROOT/'quota.mjs').as_uri())+')).collect('+json.dumps(CACHE.as_posix())+')'
        try:
            if not exe:
                raise FileNotFoundError('ego-browser')
            ensure_ego_background(exe)
            worker=subprocess.Popen([exe,'nodejs','-e',script],creationflags=subprocess.CREATE_NO_WINDOW,
                                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            error=False
        except (OSError, subprocess.SubprocessError):
            error=True
        update_view()

    def resize():
        nonlocal width,height,scale,mask_size
        if not attached:
            return
        rect=wt.RECT()
        USER.GetClientRect(attached,ct.byref(rect))
        point=wt.POINT(0,0)
        USER.ClientToScreen(attached,ct.byref(point))
        scale=(USER.GetDpiForWindow(attached) or 96)/96
        width,height=(card_x+card_w,card_h) if expanded else (30,30)
        USER.SetWindowPos(hwnd,0,point.x+round(10*scale),point.y+rect.bottom-round((height+42)*scale),
                          round(width*scale),round(height*scale),0x0054)
        if mask_size!=(width,height,scale):
            gdi=ct.windll.gdi32
            def region(x,y,w,h,r):
                return gdi.CreateRoundRectRgn(*(round(v*scale) for v in (x,y,x+w+1,y+h+1,r,r)))
            shape=region(0,height-30,30,30,10)
            if expanded:
                card=region(card_x,0,card_w,card_h,20)
                gdi.CombineRgn(shape,shape,card,2)
                gdi.DeleteObject(card)
            if USER.SetWindowRgn(hwnd,shape,True):
                mask_size=(width,height,scale)
            else:
                gdi.DeleteObject(shape)

    def set_open(value):
        nonlocal expanded
        expanded=value
        resize()
        draw()
        if value and time.monotonic()-last_attempt>600:
            fetch()

    def hit(event):
        x,y=event.x/scale,event.y/scale
        if x<=30 and y>=height-30:
            return 'icon'
        if expanded and 131<=y<=card_h:
            if width-29<=x<=width: return 'close'
            if width-80<=x<width-29: return 'refresh'
        return None

    def click(event):
        part=hit(event)
        if part=='icon': set_open(not expanded)
        elif part=='close': set_open(False)
        elif part=='refresh': fetch()

    def motion(event):
        nonlocal hover
        part=hit(event) if event.type!=tk.EventType.Leave else None
        if part!=hover:
            hover=part
            canvas.configure(cursor='hand2' if part else '')
            draw()

    canvas.bind('<ButtonRelease-1>',click)
    canvas.bind('<Motion>',motion)
    canvas.bind('<Leave>',motion)
    update_view()
    root.update_idletasks()
    hwnd=USER.GetParent(root.winfo_id()) or root.winfo_id()

    def tick():
        nonlocal attached,worker,error
        if KERNEL.WaitForSingleObject(process,0)==0:
            root.destroy()
            return
        if worker and (worker.poll() is not None or time.monotonic()-started>65):
            if worker.poll() is None:
                worker.kill()
            error=worker.poll()!=0
            worker=None
            update_view()
        owner=attached if attached and USER.IsWindow(attached) else find_window(pid)
        if owner:
            if owner!=attached:
                USER.SetWindowLongPtrW(hwnd,-8,owner)
                USER.SetWindowLongPtrW(hwnd,-20,USER.GetWindowLongPtrW(hwnd,-20)|0x08000080)
                attached=owner
            if USER.IsIconic(owner) or not USER.IsWindowVisible(owner):
                USER.ShowWindow(hwnd,0)
            else:
                previous_scale=scale
                resize()
                if previous_scale!=scale:
                    draw()
                if expanded:
                    here=wt.POINT()
                    USER.GetCursorPos(ct.byref(here))
                    bounds=wt.RECT()
                    USER.GetWindowRect(hwnd,ct.byref(bounds))
                    x,y=(here.x-bounds.left)/scale,(here.y-bounds.top)/scale
                    outside=not ((card_x<=x<width and 0<=y<height) or
                                 (0<=x<30 and height-30<=y<height))
                    if ((outside and USER.GetAsyncKeyState(1)&0x8001)
                            or (USER.GetForegroundWindow() in (owner,hwnd) and USER.GetAsyncKeyState(27)&0x8001)):
                        set_open(False)
                if (USER.GetForegroundWindow() in (owner,hwnd) and time.monotonic()-last_attempt>600
                        and not any(STATE.glob('tmp*/job.json'))):
                    fetch()
        root.after(25 if expanded else 350,tick)
    root.deiconify()
    tick()
    root.mainloop()


if __name__=='__main__':
    monitor(int(sys.argv[1]))
