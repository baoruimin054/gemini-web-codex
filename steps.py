"""Durable bridge steps; all filesystem operations are dispatched via Codex."""
import json
import os
from pathlib import Path
import re
import secrets
import sys
from conversation import fingerprint

HELPER=Path(__file__).with_name('artifact_ops.py').resolve()


ARTIFACT_PYTHON=HELPER.parent/'.artifact-venv'/'Scripts'/'python.exe'


def paths_of(values):
    if not isinstance(values,list) or not 0<len(values)<=32:
        raise ValueError('expected 1 to 32 absolute file paths')
    for value in values:
        if not isinstance(value,str) or not re.match(r'^[A-Za-z]:[\\/]',value) or any(c in value for c in '\r\n\0'):
            raise ValueError('expected a local absolute file path')
    return list(dict.fromkeys(values))


def shell_step(mode,token,paths,tools):
    if tools.get('exec_command',{}).get('type')!='function':
        raise ValueError('原生 exec_command 不可用')
    def quote(value): return "'"+str(value).replace("'","''")+"'"
    args=[ARTIFACT_PYTHON if ARTIFACT_PYTHON.is_file() else sys.executable,'-X','utf8',HELPER,mode,token,*paths_of(paths)]
    return {'tool':'exec_command','arguments':{'cmd':'& '+' '.join(map(quote,args))}}


def output_of(items,call_id):
    for pos,item in reversed(list(enumerate(items))):
        if isinstance(item,dict) and item.get('type') in ('function_call_output','custom_tool_call_output') and item.get('call_id')==call_id:
            if any(isinstance(i,dict) and i.get('role')=='user' for i in items[pos+1:]): return None
            return item.get('output','')
    return None


def receipt_ok(output,token,paths):
    if not isinstance(output,str): return False
    for line in output.splitlines():
        if not line.startswith('GEMINI_RECEIPT '): continue
        try:
            r=json.loads(line[15:])
            return r['token']==token and [os.path.normcase(f['path']) for f in r['files']]==[os.path.normcase(p) for p in paths]
        except (ValueError,KeyError,TypeError): pass
    return False


class Steps:
    def __init__(self,state,thread):
        self.file=None
        self.data={}
        if thread:
            directory=state/'steps'
            directory.mkdir(exist_ok=True,parents=True)
            self.file=directory/(fingerprint(thread)+'.json')
            if self.file.exists(): self.data=json.loads(self.file.read_text(encoding='utf8'))

    def save(self):
        if self.file:
            tmp=self.file.with_suffix('.new')
            tmp.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf8')
            os.replace(tmp,self.file)

    def dispatch(self,obj,tools,convert,phase,req,**fields):
        item=convert(obj,tools)
        self.data['pending']=dict(phase=phase,call_id=item['call_id'],request=req,**fields)
        self.save()
        return item

    def resume(self,req,tools,convert):
        request_key=fingerprint(req)
        pending=self.data.get('pending')
        if not pending:
            items=req.get('input',[])
            last=items[-1] if isinstance(items,list) and items else None
            message=items if isinstance(items,str) else json.dumps(last.get('content',''),ensure_ascii=False) if isinstance(last,dict) and last.get('role')=='user' else ''
            if re.search(r'撤销|回滚|\bundo\b|\broll back\b',message,re.I) and self.data.get('changes') and 'exec_command' in tools:
                entry=self.data['changes'][-1]
                cmd=shell_step('undo',entry['token'],entry['paths'],tools)['arguments']['cmd']
                return None,'Latest sealed files: '+json.dumps(entry['paths'],ensure_ascii=False)+'. Only if the user requested undo of these files use: '+cmd
            return None,''
        phase=pending['phase']
        items=req.get('input',[])
        output=output_of(items,pending['call_id']) if isinstance(items,list) else None
        hint=''
        if output is None:
            hint='Previous tool dispatch has no matching receipt. Inspect its effects before retrying. '
            if pending.get('paths'): hint+='Expected files: '+json.dumps(pending['paths'],ensure_ascii=False)
            # Never repeat a dispatched side effect solely because the transport retried.
            if request_key==pending['request'] and pending.get('paths'):
                paths=pending['paths']
                token=secrets.token_hex(12)
                return self.dispatch(shell_step('verify',token,paths,tools),tools,convert,'inspect',request_key,paths=paths,token=token), ''
            if request_key==pending['request']:
                # An arbitrary shell command can have effects we cannot inspect
                # from the gateway. Do not ask the model to recreate it.
                return convert({'answer':'上一条本机工具调用已派发，重连时未收到执行回执。为避免重复执行，需核实其实际结果。'},tools),''
        if phase in ('before','seal','verify') and receipt_ok(output,pending['token'],pending['paths']):
            self.data['acknowledged']=(self.data.get('acknowledged',[])+[pending['call_id']])[-64:]
        if phase=='before' and receipt_ok(output,pending['token'],pending['paths']):
            fields={k:pending[k] for k in ('paths','token')}
            return self.dispatch(pending['action'],tools,convert,'write',request_key,**fields),''
        elif phase=='write' and output is not None:
            paths,token=pending['paths'],pending['token']
            running=re.search(r'^Process running with session ID (\d+)\s*$',str(output),re.M|re.I)
            if running and 'write_stdin' in tools:
                poll={'tool':'write_stdin','arguments':{'session_id':int(running[1]),'chars':'','yield_time_ms':15000}}
                return self.dispatch(poll,tools,convert,'write',request_key,paths=paths,token=token),''
            return self.dispatch(shell_step('seal',token,paths,tools),tools,convert,'seal',request_key,paths=paths,token=token),''
        elif phase=='seal' and receipt_ok(output,pending['token'],pending['paths']):
            entry={k:pending[k] for k in ('paths','token')}
            self.data.setdefault('changes',[]).append(entry)
            hint='Checkpoint saved for declared writes. Use the actual command result to continue.'
        elif phase=='verify' and receipt_ok(output,pending['token'],pending['paths']):
            item=convert({'answer':pending['answer']},tools)
            self.data['verified']=dict(paths=pending['paths'],token=pending['token'])
            self.data.pop('pending')
            self.data['final_cache']=[fingerprint(req),item]
            self.save()
            return item,''
        elif phase in ('before','seal','verify','inspect'):
            hint='Check the actual file/tool result and fix any failed check. Do not claim unverified completion.'
        if output is not None or request_key!=pending['request']:
            self.data.pop('pending',None)
            self.save()
        return None,hint

    def accept(self,obj,req,tools,convert):
        token=secrets.token_hex(12)
        if self.file and obj.get('artifacts') and 'answer' in obj:
            paths=paths_of(obj['artifacts'])
            return self.dispatch(shell_step('verify',token,paths,tools),tools,convert,'verify',req,
                                 paths=paths,token=token,answer=obj['answer'])
        if self.file and obj.get('writes') and 'tool' in obj:
            paths=paths_of(obj['writes'])
            action={k:v for k,v in obj.items() if k!='writes'}
            convert(action,tools)
            return self.dispatch(shell_step('before',token,paths,tools),tools,convert,'before',req,
                                 paths=paths,token=token,action=action)
        if self.file and 'tool' in obj:
            return self.dispatch(obj,tools,convert,'tool',req)
        return convert(obj,tools)
