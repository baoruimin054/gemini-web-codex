"""Prepare a turn for a single Gemini web conversation."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re

from PIL import Image


def fingerprint(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(data.encode()).hexdigest()


def prepare(req, directory, state, thread, fresh=False):
    folder = state / 'conversations'
    folder.mkdir(exist_ok=True)
    record = folder / (fingerprint(thread) + '.json') if thread else None
    session = json.loads(record.read_text(encoding='utf8')) if record and record.exists() else {}
    if fresh:
        if not session.get('url'):
            raise ValueError('恢复请求缺少原 Gemini 对话地址')
    items = req.get('input', [])
    if isinstance(items, str):
        items = [dict(role='user', content=items)]
    hashes = [fingerprint(item) for item in items]
    old = session.get('history', [])
    # Recovery stays in the same Gemini conversation. It already contains
    # every verified prefix turn, including earlier completed tool results.
    offset = len(old) if session.get('url') and old == hashes[:len(old)] and len(old)<len(hashes) else 0
    images = []
    for item in items[offset:]:
        if not isinstance(item,dict) or not isinstance(item.get('content'),list):
            continue
        for i, part in enumerate(item['content']):
            if not isinstance(part,dict) or part.get('type') != 'input_image':
                continue
            match = re.fullmatch(r'data:image/(?:png|jpeg|jpg|webp|gif);base64,([A-Za-z0-9+/=]+)', part.get('image_url',''), re.I)
            if not match:
                raise ValueError('请直接附加本地 PNG、JPEG、WebP 或 GIF 图片。')
            data = base64.b64decode(match[1], validate=True)
            with Image.open(io.BytesIO(data)) as image:
                suffix = {'PNG':'png','JPEG':'jpg','WEBP':'webp','GIF':'gif'}.get(image.format)
                if not suffix or image.width*image.height > 60_000_000:
                    raise ValueError('图片格式或尺寸超出范围')
                image.verify()
            file = directory / f'attachment-{len(images)+1}.{suffix}'
            file.write_bytes(data)
            images.append(str(file))
            item['content'][i] = dict(type='input_text',text=f'[Attached image: {file.name}]')
    return items[offset:], images, record, session, hashes


def tool_payload(tools,session,hashes,model,instructions=''):
    definitions=list(tools.values())
    signature=fingerprint(['protocol-3',model,instructions,sorted(definitions,key=lambda t:t['name'])])
    old=session.get('history',[])
    reusable=(session.get('url') and old and len(old)<len(hashes) and hashes[:len(old)]==old
              and session.get('tool_signature')==signature)
    if reusable:
        return {},signature,session.get('tool_reuses',0)+1
    return {'instructions':instructions,'tools':definitions},signature,0


def compact_logs(items,state,thread):
    if not thread: return
    outputs=[i for i in items if isinstance(i,dict) and i.get('type') in ('function_call_output','custom_tool_call_output')
             and isinstance(i.get('output'),str)]
    limit=min(12000,max(800,60000//max(1,len(outputs))))
    for item in outputs:
        log=item.get('output')
        if len(log)<=limit: continue
        folder=state/'logs'/fingerprint(thread)
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/(fingerprint(log)+'.txt')
        if not path.exists(): path.write_text(log,encoding='utf8')
        keep=limit//3
        item['output']=log[:keep]+f'\n[中间省略，完整工具输出共 {len(log)} 字符，按需分段读取 {path}]\n'+log[-keep:]


def skip_echo(items, session):
    if not items or not session.get('echo'): return items
    first=items[0]
    if not isinstance(first,dict): return items
    kind=first.get('type')
    if kind=='function_call':
        try: obj={'tool':first['name'],'arguments':json.loads(first['arguments'])}
        except (KeyError,ValueError,TypeError): return items
    elif kind=='custom_tool_call':
        obj={'tool':first.get('name'),'input':first.get('input')}
    elif first.get('role')=='assistant':
        parts=first.get('content')
        if not isinstance(parts,list) or len(parts)!=1 or not isinstance(parts[0],dict): return items
        obj={'answer':parts[0].get('text')}
    else: return items
    return items[1:] if fingerprint(obj)==session['echo'] else items


def save_history(record, hashes, signature, reuses, obj=None, sent=()):
    if record:
        session = json.loads(record.read_text(encoding='utf8'))
        session.pop('inflight',None)
        session['history'] = hashes
        session.update(tool_signature=signature,tool_reuses=reuses)
        if sent:
            session['recent_sends']=(session.get('recent_sends',[])+[{'compact':bool(reuses),'chars':n} for n in sent])[-32:]
        if obj:
            session['echo']=fingerprint({k:v for k,v in obj.items() if k in ('tool','arguments','input','answer')})
        temp = record.with_suffix('.tmp')
        temp.write_text(json.dumps(session),encoding='utf8')
        os.replace(temp,record)

def delivery_contract(req):
    """Bound final-answer checks to the most recent explicit file-creation request."""
    items=req.get('input',[])
    if isinstance(items,str): items=[{'role':'user','content':items}]
    requested=''
    outputs=[]
    for item in items:
        if not isinstance(item,dict): continue
        if item.get('role')=='user':
            content=item.get('content','')
            if isinstance(content,list):
                content='\n'.join(str(p.get('text','')) for p in content if isinstance(p,dict))
            if not isinstance(content,str) or content.lstrip().startswith(('<environment_context>','<permissions instructions>')): continue
            requested=content
            outputs=[]
        if item.get('type') in ('function_call_output','custom_tool_call_output'):
            outputs.append(str(item.get('output',''))[:4000])
    want=bool(re.search(r'创建|新建|生成|写入|导出|保存|\b(create|generate|export|save|write|replace)\b',requested,re.I))
    formats=re.findall(r'\.(docx|pdf|mp4|html|txt|xlsx|pptx)\b',requested,re.I)
    if not formats and want:
        formats=[v for k,v in [('Word','docx'),('PDF','pdf')] if re.search(k,requested,re.I)]
    if not want or not formats: return {}
    # Format-level validation also works for Chinese sentences with no space before filenames.
    return dict(formats=sorted(set(s.lower() for s in formats)),
                blocker=any(re.search(r'denied|rejected|failed|拒绝|错误|exited with code [1-9]|error:',v,re.I) for v in outputs))
