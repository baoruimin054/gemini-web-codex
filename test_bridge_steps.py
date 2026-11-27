import json
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch
from artifact_ops import checkpoint, inspect
from conversation import compact_logs, fingerprint, skip_echo, tool_payload
from gateway import reply_item
from steps import Steps

TOOLS={'exec_command':{'name':'exec_command','type':'function','parameters':{'type':'object'}}}


class BridgeTests(unittest.TestCase):
    def test_contract_refresh_and_logs(self):
        h=['first']
        data,signature,_=tool_payload(TOOLS,{},h,'model','rules')
        self.assertIn('tools',data)
        previous=dict(url='chat',history=h,tool_signature=signature,tool_reuses=80)
        self.assertEqual(tool_payload(TOOLS,previous,h+['second'],'model','rules')[0],{})
        for tools,model,rules in [({},'model','rules'),(TOOLS,'other','rules'),(TOOLS,'model','changed')]:
            self.assertIn('tools',tool_payload(tools,previous,h+['second'],model,rules)[0])
        action={'tool':'exec_command','arguments':{'cmd':'pwd'}}
        echo={'type':'function_call','name':'exec_command','arguments':'{"cmd":"pwd"}'}
        self.assertEqual(skip_echo([echo,'next'],{'echo':fingerprint(action)}),['next'])
        with tempfile.TemporaryDirectory() as t:
            log='first\n'+'x'*90000+'\nlast'
            out=[dict(type='function_call_output',output=log)]
            compact_logs(out,Path(t),'fixture')
            self.assertLess(len(out[0]['output']),8500)
            self.assertEqual(next(Path(t).rglob('*.txt')).read_text(),log)

    def test_resume_and_missing_receipt(self):
        with tempfile.TemporaryDirectory() as t:
            path=str(Path(t)/'x.txt')
            req={'input':[{'role':'user','content':'write file'}]}
            action={'tool':'exec_command','arguments':{'cmd':'write'},'writes':[path]}
            def steps():return Steps(Path(t),'fixture')
            first=steps().accept(action,fingerprint(req),TOOLS,reply_item)
            token=steps().data['pending']['token']
            def result(call,text):
                req['input'].append(dict(type='function_call_output',call_id=call['call_id'],output=text))
            receipt='GEMINI_RECEIPT '+json.dumps(dict(token=token,files=[dict(path=path)]))
            result(first,receipt)
            write,_=steps().resume(req,TOOLS,reply_item)
            self.assertEqual(json.loads(write['arguments']),{'cmd':'write'})
            # Restart after dispatch but before a receipt: inspect file; do not issue the write twice.
            recovery,_=steps().resume(req,TOOLS,reply_item)
            self.assertIn("'verify'",json.loads(recovery['arguments'])['cmd'])

    def test_verify_after_restart_and_undo(self):
        with tempfile.TemporaryDirectory() as tmp,patch('tempfile.gettempdir',return_value=tmp):
            root=Path(tmp);path=root/'report.txt';path.write_text('old')
            token=secrets.token_hex(12)
            checkpoint('before',token,path);path.write_text('new');checkpoint('seal',token,path)
            path.write_text('manual edit')
            with self.assertRaises(ValueError):checkpoint('undo',token,path)
            path.write_text('new');checkpoint('undo',token,path)
            self.assertEqual(path.read_text(),'old')
            req={'input':[]};s=Steps(root,'verify')
            call=s.accept({'answer':'done','artifacts':[str(path)]},fingerprint(req),TOOLS,reply_item)
            receipt=dict(token=s.data['pending']['token'],files=[inspect(path)])
            req['input'].append(dict(type='function_call_output',call_id=call['call_id'],output='GEMINI_RECEIPT '+json.dumps(receipt)))
            done,_=Steps(root,'verify').resume(req,TOOLS,reply_item)
            self.assertEqual(done['content'][0]['text'],'done')

    def test_stream_disconnect_cancels_running_worker(self):
        import http.client
        import threading
        import gateway
        started=threading.Event()
        stopped=threading.Event()
        def worker(req,thread,progress,cancelled):
            started.set()
            if cancelled.wait(8): stopped.set()
            raise ConnectionResetError()
        with (tempfile.TemporaryDirectory() as directory, patch.object(gateway,'STATE',Path(directory)),
              patch.object(gateway,'web_reply',worker),
              gateway.ThreadingHTTPServer(('127.0.0.1',0),gateway.Handler) as server):
            server.key='test-only'
            service=threading.Thread(target=server.serve_forever,daemon=True)
            service.start()
            try:
                conn=http.client.HTTPConnection(*server.server_address,timeout=5)
                body=json.dumps(dict(model='gemini-web-4-hydrogen',stream=True,input='fixture'))
                conn.request('POST','/v1/responses',body,{'Authorization':'Bearer test-only','thread-id':'cancel-fixture'})
                response=conn.getresponse()
                self.assertEqual(response.status,200)
                self.assertTrue(started.wait(3))
                response.close()
                conn.close()
                self.assertTrue(stopped.wait(5))
            finally:
                server.shutdown(); service.join(3)
