"""A non-activating quota panel owned by the dedicated Gemini desktop window."""
import ctypes as ct
from ctypes import wintypes as wt
from datetime import datetime
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageDraw, ImageTk
from ego_installation import ego_binary, ego_cli, ensure_ego_background, preferred_profile
from account_selection import load_account_file, refresh_command, select_account, selected_account

STATE = Path(os.environ['LOCALAPPDATA']) / 'CodexGeminiWeb'
CACHE = STATE / 'quota.json'
WINDOW_ICON = Path(__file__).with_name('Gemini-Codex.ico')
ACCOUNT_STATUS_TEXT = {
    'login_required': 'Gemini 未登录',
    'verification_required': 'Google 网页需要验证',
    'region_unavailable': 'Gemini 网页提示地区不可用',
    'account_unavailable': '所选 Gemini 账号不可用',
    'page_unavailable': 'Gemini 网页暂时无法读取',
}
USER = ct.windll.user32
KERNEL = ct.windll.kernel32
GDI = ct.windll.gdi32
USER.GetDC.argtypes = [wt.HWND]
USER.GetDC.restype = wt.HDC
USER.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
GDI.CreateCompatibleDC.argtypes = [wt.HDC]
GDI.CreateCompatibleDC.restype = wt.HDC
GDI.DeleteDC.argtypes = [wt.HDC]
GDI.CreateDIBSection.argtypes = [wt.HDC, ct.c_void_p, wt.UINT, ct.POINTER(ct.c_void_p), wt.HANDLE, wt.DWORD]
GDI.CreateDIBSection.restype = wt.HBITMAP
GDI.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
GDI.SelectObject.restype = wt.HGDIOBJ
GDI.DeleteObject.argtypes = [wt.HGDIOBJ]
GDI.BitBlt.argtypes = [wt.HDC, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.HDC, ct.c_int, ct.c_int, wt.DWORD]
USER.GetParent.argtypes = [wt.HWND]
USER.GetParent.restype = wt.HWND
USER.SetWindowLongPtrW.argtypes = [wt.HWND, ct.c_int, ct.c_ssize_t]
USER.SetWindowLongPtrW.restype = ct.c_ssize_t
USER.GetWindowLongPtrW.argtypes = [wt.HWND, ct.c_int]
USER.GetWindowLongPtrW.restype = ct.c_ssize_t
MOUSE_HOOK = ct.WINFUNCTYPE(ct.c_ssize_t, ct.c_int, wt.WPARAM, wt.LPARAM)
USER.SetWindowsHookExW.argtypes = [ct.c_int, MOUSE_HOOK, wt.HINSTANCE, wt.DWORD]
USER.SetWindowsHookExW.restype = wt.HHOOK
USER.CallNextHookEx.argtypes = [wt.HHOOK, ct.c_int, wt.WPARAM, wt.LPARAM]
USER.CallNextHookEx.restype = ct.c_ssize_t
USER.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
USER.WindowFromPoint.argtypes = [wt.POINT]
USER.WindowFromPoint.restype = wt.HWND
USER.GetAncestor.argtypes = [wt.HWND, wt.UINT]
USER.GetAncestor.restype = wt.HWND
USER.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ct.c_int]
USER.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
USER.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ct.c_int, ct.c_int, wt.UINT]
USER.LoadImageW.restype = wt.HANDLE
USER.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
                                    ct.POINTER(ct.c_size_t)]
USER.SendMessageTimeoutW.restype = ct.c_ssize_t
KERNEL.GetModuleHandleW.argtypes = [wt.LPCWSTR]
KERNEL.GetModuleHandleW.restype = wt.HMODULE
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


def brand_window_title(hwnd):
    title = ct.create_unicode_buffer(256)
    USER.GetWindowTextW(hwnd,title,len(title))
    if title.value.casefold() in ('codex','chatgpt'):
        USER.SetWindowTextW(hwnd,'GemCodex')


def brand_window_icons(hwnd, icons):
    for kind, icon in enumerate(icons):
        if icon:
            previous = ct.c_size_t()
            USER.SendMessageTimeoutW(hwnd,0x0080,kind,icon,0x0002,150,ct.byref(previous))  # WM_SETICON


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


def sidebar_sample(x, top, bottom, width):
    """Read five scanlines from one small desktop copy."""
    screen = USER.GetDC(None)
    if not screen:
        return None
    memory = GDI.CreateCompatibleDC(screen)
    height = bottom-top
    info = struct.pack('<IiiHHIIiiII',40,width,-height,1,32,0,0,0,0,0,0)
    data = ct.c_void_p()
    bitmap = GDI.CreateDIBSection(screen,info,0,ct.byref(data),None,0) if memory else None
    old = GDI.SelectObject(memory,bitmap) if bitmap else None
    try:
        if not old or not GDI.BitBlt(memory,0,0,width,height,screen,x,top,0x00CC0020):
            return None
        GDI.GdiFlush()
        stride = width*4
        lines = (ct.string_at(data.value+round((height-1)*part/6)*stride,stride) for part in range(1,6))
        return Image.frombytes('RGB',(width,5),b''.join(lines),'raw','BGRX')
    finally:
        if old:
            GDI.SelectObject(memory,old)
        if bitmap:
            GDI.DeleteObject(bitmap)
        if memory:
            GDI.DeleteDC(memory)
        USER.ReleaseDC(None,screen)


def sidebar_edge(image, scale):
    """Find the background boundary shared by several rows of the desktop."""
    pixels = image.load()
    rows = range(image.height)
    step = max(2,round(4*scale))
    reference = round(115*scale)
    if image.width <= reference:
        return 0
    for x in range(round(160*scale),image.width-3*step):
        votes = 0
        for y in rows:
            before,after = pixels[x-step,y],pixels[x+step,y]
            baseline = pixels[reference,y]
            if (sum(abs(a-b) for a,b in zip(before,baseline)) <= 6
                    and sum(abs(a-b) for a,b in zip(after,before)) >= 9
                    and after == pixels[x+3*step,y]):
                votes += 1
        if votes >= 4:
            return x / scale
    return 0


def monitor(pid):
    # One panel per desktop process, including repeated shortcut launches.
    mutex = KERNEL.CreateMutexW(None, False, f'Local\\GeminiWebQuota-{pid}')
    if not mutex or KERNEL.GetLastError() == 183:
        return
    process = KERNEL.OpenProcess(0x100000, False, pid)
    if not process:
        return
    icons = [USER.LoadImageW(None,str(WINDOW_ICON),1,size,size,0x0010) for size in (16,32)]
    next_brand_check = 0
    ct.windll.shcore.SetProcessDpiAwareness(2)
    root = tk.Tk()
    root.withdraw()
    root.title('GemCodex 账号和额度')
    root.overrideredirect(True)
    # Keep the compact button anchored to the rail even while its card is open.
    clear = '#f0f3f9'
    root.configure(bg=clear)
    canvas = tk.Canvas(root, bg=clear, bd=0, highlightthickness=0)
    canvas.pack(fill='both', expand=True)
    expanded = False
    mouse_hook = None
    dismiss_requested = False
    menu_open = False
    worker = None
    refresh_pending = False
    last_attempt = 0
    started = 0
    error = False
    data = {}
    scale = 1
    attached = None
    was_focused = False
    hover = None
    card_x, card_h, card_bottom = 48, 238, 53
    panel_w = 0
    next_measure = 0
    placement = None
    actions_top = card_h-44
    footer_h, footer_bottom, rail_bottom = 34, 10, 42
    width, height = 30, rail_bottom+30
    mask_size = None

    def draw():
        canvas.delete('all')
        account=data.get('account') or {}
        account_status=ACCOUNT_STATUS_TEXT.get(data.get('status'))
        if not account_status:
            account_status='账号检查失败' if error else '正在检查 Gemini 登录…'
        def xy(*coords):
            return [round(v * scale) for v in coords]
        def text(x,y,value,color='#303030',size=14,anchor='w',limit=None):
            pixel_size=-max(9,round(size*scale))
            if limit is not None:
                font=tkfont.Font(root=root,family='Segoe UI',size=pixel_size)
                if font.measure(value)>limit*scale:
                    while value and font.measure(value+'…')>limit*scale:
                        value=value[:-1]
                    value+='…'
            canvas.create_text(*xy(x,y),text=value,fill=color,anchor=anchor,
                               font=('Segoe UI',pixel_size))
        def rounded(x,y,w,h,r,fill,outline=''):
            pts=[x+r,y,x+w-r,y,x+w,y,x+w,y+r,x+w,y+h-r,x+w,y+h,x+w-r,y+h,
                 x+r,y+h,x,y+h,x,y+h-r,x,y+r,x,y]
            canvas.create_polygon(*xy(*pts),smooth=True,splinesteps=18,fill=fill,outline=outline)
        if expanded and panel_w:
            rounded(card_x+1,1,panel_w-2,card_h-2,13,'#ffffff','#e1e1e1')
            left,right=card_x+14,card_x+panel_w-15
            text(left,19,account.get('label') or account_status,'#303030',14,limit=right-left-63)
            text(right,19,'切换账号','#3158a5',13,'e')
            models=account.get('models',[])
            if models:
                text(left,40,'可用模型  '+ ' · '.join(model.replace('4 ','') for model in models),
                     '#747474',12,limit=right-left)
            elif data.get('status') in ('login_required','verification_required'):
                text(left,40,'可从「切换账号」打开 Gemini 网页','#747474',12)
            canvas.create_line(*xy(left,58,right,58),fill='#ececec')
            for idx,(key,title) in enumerate((('five','5 小时用量上限'),('week','每周限额'))):
                y=70+idx*53
                entry=data.get(key,{})
                text(left,y+10,title)
                remaining=entry.get('remaining')
                text(right,y+10,f'剩余 {remaining}%' if remaining is not None else '未显示',anchor='e')
                text(left,y+29,reset_text(entry.get('reset','')),'#686868',12)
            checked=data.get('checked')
            status=datetime.fromtimestamp(checked/1000).strftime('%H:%M 更新') if checked else '尚未读取'
            canvas.create_line(*xy(left,actions_top,right,actions_top),fill='#ececec')
            text(left,actions_top+21,'读取中…' if worker else ('刷新失败' if error else status),'#707070',12)
            text(right-27,actions_top+21,'刷新','#444444',13,'e')
            text(right-1,actions_top+21,'×','#666666',17,'center')
        if panel_w:
            draw_footer(text, rounded, xy, account, account_status)
        draw_ring(xy)

    def draw_footer(text, rounded, xy, account, account_status):
        footer_y=height-footer_bottom-footer_h
        rounded(card_x,footer_y,panel_w,footer_h,9,
                '#e6e8ed' if expanded or hover=='footer' else '#eceef2')
        name=account.get('displayName') or account.get('label')
        label=name or account_status
        center=footer_y+footer_h/2
        canvas.create_oval(*xy(card_x+9,center-10,card_x+29,center+10),fill='#e0e7f2',outline='')
        text(card_x+19,center,name[0].upper() if name else 'G','#4a5e78',11,'center')
        text(card_x+38,center,label,'#283142' if name else '#626c7b',13,limit=panel_w-67)
        arrow=card_x+panel_w-17
        canvas.create_line(*xy(arrow-3,center+1,arrow,center-2,arrow+3,center+1),
                           fill='#657187',width=max(1,scale))

    def draw_ring(xy):
        # Supersampling keeps the thin ring and diamond smooth at fractional DPI.
        remaining=data.get('five',{}).get('remaining')
        highlight=expanded or hover=='icon'
        icon_key=(scale,highlight,remaining)
        if getattr(canvas,'icon_key',None)==icon_key:
            canvas.create_image(*xy(0,height-rail_bottom-30),image=canvas.icon,anchor='nw')
            return
        unit=12
        icon=Image.new('RGB',(30*unit,30*unit),clear)
        pen=ImageDraw.Draw(icon)
        def bounds(*values):
            return tuple(round(v*unit) for v in values)
        if highlight:
            pen.ellipse(bounds(2,2,28,28),fill='#e5e9ef')
        arc=bounds(6,6,24,24)
        pen.ellipse(arc,outline='#cbd0d7',width=16)
        if isinstance(remaining,(int,float)) and remaining>0:
            pen.arc(arc,-90,-90+3.6*min(100,remaining),fill='#565e69',width=21)
        pen.polygon(bounds(15,11.5,18.5,15,15,18.5,11.5,15),fill='#9097a1')
        size=round(30*scale)
        canvas.icon=ImageTk.PhotoImage(icon.resize((size,size),Image.Resampling.LANCZOS),master=canvas)
        canvas.icon_key=icon_key
        canvas.create_image(*xy(0,height-rail_bottom-30),image=canvas.icon,anchor='nw')

    def update_view():
        nonlocal data
        data=load_account_file(CACHE)
        chosen=selected_account(STATE)
        active=data.get('account') or {}
        if chosen and (active.get('id'),active.get('slot'))!=(chosen['id'],chosen['slot']):
            data['account']=next((entry for entry in data.get('accounts',[])
                if (entry['id'],entry['slot'])==(chosen['id'],chosen['slot'])),None)
            data.pop('five',None)
            data.pop('week',None)
        draw()

    def fetch():
        nonlocal worker,last_attempt,started,error
        if worker:
            return
        last_attempt=started=time.monotonic()
        exe=ego_cli()
        try:
            if not exe:
                raise FileNotFoundError('ego-browser')
            ensure_ego_background(exe)
            worker=subprocess.Popen(refresh_command(exe,STATE),creationflags=subprocess.CREATE_NO_WINDOW,
                                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            error=False
        except (OSError, subprocess.SubprocessError):
            error=True
        update_view()

    def resize():
        nonlocal width,height,scale,mask_size,panel_w,next_measure,placement
        if not attached:
            return
        rect=wt.RECT()
        USER.GetClientRect(attached,ct.byref(rect))
        point=wt.POINT(0,0)
        USER.ClientToScreen(attached,ct.byref(point))
        scale=(USER.GetDpiForWindow(attached) or 96)/96
        now=time.monotonic()
        dragging=bool(USER.GetAsyncKeyState(1)&0x8000)
        if (dragging or now>=next_measure) and USER.GetForegroundWindow() in (attached,hwnd):
            next_measure=now+0.12
            right=min(rect.right,round(580*scale))
            top=round(125*scale)
            bottom=min(rect.bottom-round((card_h+card_bottom)*scale),round(500*scale))
            if right>round(180*scale) and bottom>top and all(
                    USER.GetAncestor(USER.WindowFromPoint(wt.POINT(point.x+x,point.y+y)),2)==attached
                    for x,y in ((right-1,top),(right-1,bottom-1),(round(120*scale),top))):
                sample=sidebar_sample(point.x,point.y+top,point.y+bottom,right)
                if sample is not None:
                    edge=sidebar_edge(sample,scale)
                    panel_w=max(0,round(edge)-card_x-19) if edge else 0
        width=card_x+panel_w if panel_w else 30
        height=card_h+card_bottom if expanded else rail_bottom+30
        geometry=(point.x+round(10*scale),point.y+rect.bottom-round(height*scale),
                  round(width*scale),round(height*scale))
        if geometry!=placement or not USER.IsWindowVisible(hwnd):
            USER.SetWindowPos(hwnd,0,*geometry,0x0054)
            placement=geometry
        if mask_size!=(width,height,scale):
            gdi=ct.windll.gdi32
            def region(x,y,w,h,r):
                return gdi.CreateRoundRectRgn(*(round(v*scale) for v in (x,y,x+w+1,y+h+1,r,r)))
            shape=region(0,height-rail_bottom-30,30,30,10)
            if panel_w:
                footer=region(card_x,height-footer_bottom-footer_h,panel_w,footer_h,16)
                gdi.CombineRgn(shape,shape,footer,2)
                gdi.DeleteObject(footer)
            if expanded and panel_w:
                card=region(card_x,0,panel_w,card_h,20)
                gdi.CombineRgn(shape,shape,card,2)
                gdi.DeleteObject(card)
            if USER.SetWindowRgn(hwnd,shape,True):
                mask_size=(width,height,scale)
            else:
                gdi.DeleteObject(shape)

    def set_open(value):
        nonlocal expanded,mouse_hook,dismiss_requested
        expanded=value
        dismiss_requested=False
        if value and not mouse_hook:
            mouse_hook=USER.SetWindowsHookExW(14,outside_press,KERNEL.GetModuleHandleW(None),0)
        elif not value and mouse_hook:
            USER.UnhookWindowsHookEx(mouse_hook)
            mouse_hook=None
        resize()
        draw()
        if value and time.monotonic()-last_attempt>120:
            fetch()

    def hit(x,y):
        if 0<=x<=30 and height-rail_bottom-30<=y<=height-rail_bottom:
            return 'icon'
        if panel_w and card_x<=x<=width and height-footer_bottom-footer_h<=y<=height-footer_bottom:
            return 'footer'
        if not expanded or not (card_x<=x<=width and 0<=y<=card_h):
            return None
        if width-110<=x and 5<=y<=34:
            return 'account'
        if y>=actions_top:
            if width-29<=x: return 'close'
            if width-80<=x<width-29: return 'refresh'
        return 'card'

    @MOUSE_HOOK
    def outside_press(code,message,details):
        nonlocal dismiss_requested
        if code==0 and message==0x0201 and expanded and not menu_open:  # WM_LBUTTONDOWN
            # MSLLHOOKSTRUCT starts with the screen position at the instant of the press.
            point=ct.cast(details,ct.POINTER(wt.POINT)).contents
            bounds=wt.RECT()
            if USER.GetWindowRect(hwnd,ct.byref(bounds)) and hit(
                    (point.x-bounds.left)/scale,(point.y-bounds.top)/scale) is None:
                dismiss_requested=True
        return USER.CallNextHookEx(None,code,message,details)

    def choose_account(entry):
        nonlocal refresh_pending,error
        try:
            select_account(STATE,entry)
        except OSError:
            error=True
            draw()
            return
        update_view()
        if worker:
            refresh_pending=True
        else:
            fetch()

    def open_google():
        exe=ego_cli()
        binary=ego_binary(exe) if exe else None
        if binary:
            picked=preferred_profile()
            profile=[f'--profile-directory={picked}'] if picked else []
            subprocess.Popen([str(binary),*profile,'https://gemini.google.com/app'])

    def account_menu(event):
        nonlocal menu_open
        menu=tk.Menu(root,tearoff=False)
        active=(data.get('account') or {}).get('id')
        for entry in data.get('accounts',[]):
            name=entry.get('displayName')
            identity=f"{name}  ·  {entry['label']}" if name else entry['label']
            label=('✓  ' if entry['id']==active else '    ')+identity
            if entry.get('models'):
                label+='  ('+' / '.join(entry['models'])+')'
            menu.add_command(label=label,command=lambda entry=entry:choose_account(entry))
        if not data.get('accounts'):
            menu.add_command(label='刷新已登录账号',command=fetch,state='disabled' if worker else 'normal')
        menu.add_separator()
        menu.add_command(label='在 Ego 打开 Gemini 添加账号',command=open_google)
        menu_open=True
        try:
            menu.tk_popup(event.x_root,event.y_root)
        finally:
            menu_open=False

    def click(event):
        part=hit(event.x/scale,event.y/scale)
        if part in ('icon','footer'): set_open(not expanded)
        elif part=='close': set_open(False)
        elif part=='refresh': fetch()
        elif part=='account': account_menu(event)

    def motion(event):
        nonlocal hover
        part=hit(event.x/scale,event.y/scale) if event.type!=tk.EventType.Leave else None
        if part=='card':
            part=None
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
        nonlocal attached,worker,error,refresh_pending,was_focused,next_brand_check
        tick_started=time.monotonic()
        if KERNEL.WaitForSingleObject(process,0)==0:
            root.destroy()
            return
        if worker and (worker.poll() is not None or time.monotonic()-started>100):
            if worker.poll() is None:
                worker.kill()
            error=worker.poll()!=0
            worker=None
            update_view()
            if refresh_pending:
                refresh_pending=False
                fetch()
        owner=attached if attached and USER.IsWindow(attached) else find_window(pid)
        if owner:
            if owner!=attached:
                USER.SetWindowLongPtrW(hwnd,-8,owner)
                USER.SetWindowLongPtrW(hwnd,-20,USER.GetWindowLongPtrW(hwnd,-20)|0x08000080)
                attached=owner
                brand_window_icons(owner,icons)
                next_brand_check=0
            if tick_started>=next_brand_check:
                brand_window_title(owner)
                next_brand_check=tick_started+1
            if USER.IsIconic(owner) or not USER.IsWindowVisible(owner):
                USER.ShowWindow(hwnd,0)
                was_focused=False
            else:
                previous_layout=(scale,width)
                resize()
                if previous_layout!=(scale,width):
                    draw()
                if expanded:
                    if (dismiss_requested
                            or (USER.GetForegroundWindow() in (owner,hwnd) and USER.GetAsyncKeyState(27)&0x8001)):
                        set_open(False)
                focused=USER.GetForegroundWindow() in (owner,hwnd)
                elapsed=time.monotonic()-last_attempt
                has_account=bool(data.get('account'))
                refresh_due=not last_attempt or (focused and (
                    elapsed>(600 if has_account else 90) or
                    (not has_account and not was_focused and elapsed>15)))
                was_focused=focused
                if refresh_due and not any(STATE.glob('tmp*/job.json')):
                    fetch()
        delay=max(1,16-round((time.monotonic()-tick_started)*1000)) if was_focused else 60 if expanded else 120
        root.after(delay,tick)
    root.deiconify()
    tick()
    try:
        root.mainloop()
    finally:
        if mouse_hook:
            USER.UnhookWindowsHookEx(mouse_hook)


if __name__=='__main__':
    monitor(int(sys.argv[1]))
