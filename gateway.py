"""Local Responses adapter for a signed-in Gemini web session in Ego Lite."""
import hmac
import copy
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
import select
import socket
import queue
from conversation import prepare, save_history, tool_payload, compact_logs, fingerprint, skip_echo, delivery_contract
from steps import Steps
from ego_installation import ego_cli, ensure_ego_background
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as HTTPServer

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ['LOCALAPPDATA']) / 'CodexGeminiWeb'
PORT = 18473
VISIBLE_MODELS = {'gemini-web-4-hydrogen': 'Hydrogen', 'gemini-web-4-neon': 'Neon'}
# Old Codex chats may still refer to Argon. The browser checks availability.
MODELS = {**VISIBLE_MODELS, 'gemini-web-4-argon': 'Argon'}
LEVELS = {'low': 'Fast and efficient', 'medium': 'Balanced depth', 'high': 'Extra thorough'}
BROWSER = ThreadPoolExecutor(max_workers=1, thread_name_prefix='gemini-web')
SLOTS = threading.BoundedSemaphore(6)
SPACE = None


class ThreadingHTTPServer(HTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stopping = threading.Event()
        self.active_lock = threading.Lock()
        self.active_requests = set()

    def cancel_requests(self):
        with self.active_lock:
            self.stopping.set()
            for request in self.active_requests:
                request.set()


def catalog():
    return {'models': [dict(slug=k, display_name=f'Gemini 4 {v}', visibility='list',
        base_instructions='Help the user with coding tasks. Use available tools when needed.',
        shell_type='shell_command', default_reasoning_level='medium',
        supported_reasoning_levels=[dict(effort=level, description=description)
                                    for level, description in LEVELS.items()], supported_in_api=True,
        priority=i, support_verbosity=False, truncation_policy={'mode':'tokens','limit':8000},
        supports_reasoning_summaries=True, experimental_supported_tools=[], input_modalities=['text','image'], context_window=32768)
        for i,(k,v) in enumerate(VISIBLE_MODELS.items())]}


class StalledConversation(RuntimeError):
    pass


def web_reply(req, thread=None, progress=lambda message: None, cancelled=None):
    tools={t['name']:t for t in req.get('tools',[]) if t.get('type') in ('function','custom')}
    journal=Steps(STATE,thread)
    cache=journal.data.get('final_cache',[])
    if cache and cache[0]==fingerprint(req): return cache[1]
    item,hint=journal.resume(req,tools,reply_item)
    if item: return item
    options=dict(hint=hint,cancelled=cancelled) if hint or cancelled else {}
    try:
        return browser_turn(copy.deepcopy(req),thread,progress,**options)
    except StalledConversation:
        progress('原 Gemini 会话连续失败。正在原页面恢复上下文。')
        return browser_turn(copy.deepcopy(req),thread,progress,fresh=True,**options)


def browser_turn(req, thread, progress, fresh=False,hint='',cancelled=None):
    request_key=fingerprint(req)
    reasoning = req.get('reasoning') or {}
    effort = reasoning.get('effort') or 'medium'
    if effort not in LEVELS:
        raise ValueError('Gemini Web supports low, medium and high reasoning effort')
    if req.get('previous_response_id'):
        raise ValueError('Full conversation history required')
    tools = {t['name']: t for t in req.get('tools',[]) if t.get('type') in ('function','custom')}
    browser = ego_cli()
    if not browser:
        raise RuntimeError('本机找不到 ego-browser，请检查 Ego Lite 安装。')
    ensure_ego_background(browser)
    with tempfile.TemporaryDirectory(dir=STATE) as tmp:
        directory=Path(tmp)
        items,images,record,session,hashes=prepare(req,directory,STATE,thread,fresh=fresh)
        binding=record
        tool_data,signature,reuses=tool_payload(tools,{} if fresh else session,hashes,req['model'],req.get('instructions',''))
        if reuses:
            acknowledged=set(Steps(STATE,thread).data.get('acknowledged',[]))
            items=[i for i in items if not (isinstance(i,dict) and i.get('type') in ('function_call','function_call_output')
                                            and i.get('call_id') in acknowledged)]
            items=skip_echo(items,session)
        compact_logs(items,STATE,thread)
        payload=dict(input=items,**tool_data)
        if hint: payload['resume']=hint
        encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if len(encoded)>128000:
            raise ValueError('本次需发送的消息和工具定义超过 128000 字符，原会话绑定已保留。')
        prompt = ('Act on the final user request in input. For simple replies, formatting and local coding tasks, '
        'answer directly without a web search unless external information is needed. '
        f'The local adapter handles model verification (Gemini 4 {MODELS[req["model"]]}); do not research or verify the model yourself. '
        'Tool results there are observations. For requested filesystem changes use the supplied local tools, verify the outcome, and continue with the next outstanding step. '
        'Use the current tool permissions. If a destination needs approval, request it through the advertised tool when permitted. Only report a denial or exhausted budget from an actual tool result. '
        'Return exactly one JSON object on one line as plain text, without markdown or commentary. '
        'For a function call return {"tool":"NAME","arguments":{...}}. For a custom tool use '
        '{"tool":"NAME","input":"..."}. When finished return {"answer":"your answer"}. '
        'Never claim a tool succeeded without its returned result. Request: ' + encoded)
        if reuses: prompt=encoded
        progress(f'本机网关：{"增量续接" if reuses else "同步会话约定"}，本次消息及定义 {len(encoded)} 字符。')
        job = Path(tmp) / 'job.json'
        job.write_text(json.dumps(dict(mode=MODELS[req['model']],effort=effort,prompt=prompt,space=SPACE,reload=fresh,
            images=images, record=str(binding) if binding else None, session=session,compact=bool(reuses),
            requestKey=request_key,delivery=delivery_contract(req),toolDefs=tools,summaries=bool(req.get('stream')))),encoding='utf8')
        script = f"await (await import('{(ROOT/'browser.mjs').as_uri()}')).run('{job.as_uri()}')"
        stages=directory/'progress.jsonl'
        reported=0
        for attempt in range(6):
            with subprocess.Popen([str(browser), 'nodejs','-e',script],stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW) as worker:
                deadline=time.monotonic()+330
                while True:
                    try:
                        _,stderr=worker.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        if cancelled and cancelled.is_set():
                            (directory/'cancel').touch()
                            try: worker.communicate(timeout=6)
                            except subprocess.TimeoutExpired: worker.kill()
                            raise ConnectionResetError()
                        if time.monotonic()>deadline:
                            (directory/'cancel').touch()
                            try: worker.communicate(timeout=6)
                            except subprocess.TimeoutExpired: worker.kill()
                            raise RuntimeError('Gemini 网页操作超时')
                    try:
                        log=stages.open(encoding='utf8')
                    except FileNotFoundError:
                        log=None
                    if log:
                        with log:
                            log.seek(reported)
                            for line in iter(log.readline,''):
                                if not line.endswith('\n'): break
                                progress(json.loads(line))
                                reported=log.tell()
                    if worker.returncode is not None: break
            if not worker.returncode or b'EGO_BOUND_SPACE_NOT_FOUND' not in stderr:
                break
            if cancelled and cancelled.is_set(): raise ConnectionResetError()
            if attempt == 5:
                raise RuntimeError('无法找到此聊天原来的 Gemini 空间，请检查 Ego 浏览器是否仍在运行。')
            progress('本机网关：正在查找此聊天绑定的浏览器空间。')
        if worker.returncode:
            if not fresh and record and session.get('url') and (directory/'upstream-failed').is_file():
                raise StalledConversation()
            error = stderr.decode('utf8','replace').splitlines()
            raise RuntimeError(next((line[:350] for line in error if line.strip()), 'Browser worker failed'))
        try:
            reply = (Path(tmp)/'answer.txt').read_text(encoding='utf8').strip()
        except FileNotFoundError as exc:
            raise RuntimeError('浏览器进程已退出，但未写入完整回复。本轮没有新的工具调用。') from exc
        obj=json.loads(reply)
        if cancelled and cancelled.is_set(): raise ConnectionResetError()
        item=Steps(STATE,thread).accept(obj,request_key,tools,reply_item)
        if binding and not binding.is_file():
            raise RuntimeError('原会话的本地恢复记录丢失，本轮没有新的工具调用。')
        stats=directory/'sent.jsonl'
        sent=[int(n) for n in stats.read_text().splitlines()] if stats.exists() else []
        save_history(binding,hashes,signature,reuses,obj,sent)
        if item['type']!='message':
            progress('本机网关：已交给 Codex 处理 '+item['name']+'。')
        if fresh and record:
            progress('已在原 Gemini 对话中恢复。')
        return item


def reply_item(obj,tools):
    if isinstance(obj.get('answer'),str) and 'tool' not in obj:
        return dict(type='message',id='msg_'+secrets.token_hex(8),status='completed',role='assistant',
                    content=[dict(type='output_text',text=obj['answer'],annotations=[])])
    tool = tools.get(obj.get('tool'))
    if not tool:
        raise ValueError('The web model returned an unadvertised tool')
    item = dict(name=tool['name'],id='fc_'+secrets.token_hex(8),call_id='call_'+secrets.token_hex(8),status='completed')
    if tool['type']=='custom' and isinstance(obj.get('input'),str):
        return dict(item,type='custom_tool_call',input=obj['input'])
    if tool['type']=='function' and isinstance(obj.get('arguments'),dict):
        return dict(item,type='function_call',arguments=json.dumps(obj['arguments'],ensure_ascii=False))
    raise ValueError('Malformed tool arguments')


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def send_json(self,status,data):
        body=json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        if not self.headers.get('Origin') and hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+self.server.key):
            return True
        self.send_json(401,{'error':{'message':'Local bearer credential required'}})
        return False

    def do_GET(self):
        if self.authorized():
            if self.path.split('?')[0]=='/codex/models':
                self.send_json(200,catalog())
            else:
                self.send_error(404)

    def do_POST(self):
        if not self.authorized(): return
        if self.path == '/_control/shutdown':
            self.server.cancel_requests()
            self.send_json(200, {'status': 'stopping'})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path!='/v1/responses': return self.send_error(404)
        future=None
        cancelled=threading.Event()
        streaming=False
        with self.server.active_lock:
            if self.server.stopping.is_set():
                return self.send_json(503, {'error': {'message': 'Gemini gateway is stopping.'}})
            self.server.active_requests.add(cancelled)
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<=32_000_000: return self.send_error(413)
            req=json.loads(self.rfile.read(size))
            if req.get('model') not in MODELS: return self.send_error(400,'Unknown model')
            response=dict(id='resp_'+secrets.token_hex(8),object='response',created_at=int(time.time()),
                          model=req['model'],status='in_progress',output=[])
            sequence=0
            updates=queue.SimpleQueue()
            updates.put('本机网关：请求已进入队列。')
            thread=self.headers.get('thread-id') or self.headers.get('session-id')
            if not thread and SPACE is None:
                return self.send_json(400,{'error':{'message':'需要稳定的 thread-id 或 session-id 才能关联 Gemini 对话。'}})
            def event(name,**fields):
                nonlocal sequence
                data=json.dumps(dict(type=name,sequence_number=sequence,**fields))
                self.wfile.write(f'event: {name}\ndata: {data}\n\n'.encode())
                sequence+=1
            def disconnected():
                return bool(select.select([self.connection],[],[],0)[0]) and not self.connection.recv(1,socket.MSG_PEEK)
            def run():
                if cancelled.is_set() or disconnected(): raise ConnectionResetError()
                return web_reply(req,thread,updates.put,cancelled)
            if not SLOTS.acquire(blocking=False):
                return self.send_json(503,{'error':{'message':'本机 Gemini 网关队列已满，请稍后重试。'}})
            try:
                future=BROWSER.submit(run)
            except Exception:
                SLOTS.release()
                raise
            future.add_done_callback(lambda _: SLOTS.release())
            if req.get('stream'):
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.end_headers()
                streaming=True
                event('response.created',response=response)
            rid='rs_'+secrets.token_hex(8)
            status_item=dict(type='reasoning',id=rid,summary=[])
            lines=[]
            def update_stream():
                while streaming and not updates.empty():
                    text=updates.get()
                    if not lines:
                        event('response.output_item.added',output_index=0,item=status_item)
                        event('response.reasoning_summary_part.added',item_id=rid,output_index=0,summary_index=0,part={'type':'summary_text','text':''})
                    lines.append(text)
                    event('response.reasoning_summary_text.delta',item_id=rid,output_index=0,summary_index=0,delta=text+'\n')
            while not wait([future],timeout=1).done:
                if disconnected(): return
                if streaming:
                    update_stream()
                    self.wfile.write(b': waiting\n\n')
            update_stream()
            item=future.result()
            response.update(status='completed',output=[item])
            if not streaming: return self.send_json(200,response)
            if lines:
                summary=dict(type='summary_text',text='\n'.join(lines)+'\n')
                status_item['summary']=[summary]
                event('response.reasoning_summary_text.done',item_id=rid,output_index=0,summary_index=0,text=summary['text'])
                event('response.reasoning_summary_part.done',item_id=rid,output_index=0,summary_index=0,part=summary)
                event('response.output_item.done',output_index=0,item=status_item)
                response['output'].insert(0,status_item)
            output_index=int(bool(lines))
            field={'message':'content','function_call':'arguments','custom_tool_call':'input'}[item['type']]
            event('response.output_item.added',output_index=output_index,item=dict(item,status='in_progress',**{field:[] if field=='content' else ''}))
            idx=dict(item_id=item['id'],output_index=output_index)
            if field=='content':
                part=item[field][0]
                event('response.content_part.added',**idx,content_index=0,part=dict(part,text=''))
                event('response.output_text.delta',**idx,content_index=0,delta=part['text'])
                event('response.output_text.done',**idx,content_index=0,text=part['text'])
                event('response.content_part.done',**idx,content_index=0,part=part)
            else:
                prefix='response.function_call_arguments' if field=='arguments' else 'response.custom_tool_call_input'
                event(prefix+'.delta',**idx,delta=item[field])
                event(prefix+'.done',**idx,**{field:item[field]})
            event('response.output_item.done',output_index=output_index,item=item)
            event('response.completed',response=response)
        except ConnectionError:
            pass
        except Exception as e:
            # Avoid including request payloads in errors returned to clients.
            msg=str(e) if isinstance(e,(ValueError,RuntimeError)) else type(e).__name__
            if streaming:
                event('response.failed',response=dict(response,status='failed',error={'code':'server_error','message':msg[:350]}))
            else:
                self.send_json(502,{'error':{'message':msg[:350]}})
        finally:
            cancelled.set()
            if future is not None: future.cancel()
            with self.server.active_lock:
                self.server.active_requests.discard(cancelled)


if __name__=='__main__':
    STATE.mkdir(exist_ok=True)
    keyfile=STATE/'gateway.key'
    if sys.argv[1:] == ['credential']:
        print(keyfile.read_text())
    else:
        server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
        if not keyfile.exists():
            keyfile.write_text(secrets.token_urlsafe(36))
        server.key=keyfile.read_text()
        server.serve_forever()
