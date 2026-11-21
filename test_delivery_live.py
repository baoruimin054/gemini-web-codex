"""Live Codex + Gemini test in an isolated workspace and provider."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
import sys
import socket
import time
import gateway
from conversation import fingerprint
from live_test_space import close_fixture
from web_profile import profile
from steps import ARTIFACT_PYTHON


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root=Path(tmp)
        work=root/'work'; work.mkdir()
        home=root/'client'; home.mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        key=secrets.token_urlsafe(32)
        cfg=profile().replace(':18473',':'+str(port)).split('[model_providers.gemini_web.auth]')[0]
        (home/'config.toml').write_text(cfg+'env_key="BRIDGE_FIXTURE_KEY"\n[windows]\nsandbox="elevated"\n',encoding='utf8')
        env=dict(os.environ,BRIDGE_FIXTURE_KEY=key,BRIDGE_TEST_WORK=str(work))
        def start():
            return subprocess.Popen([sys.executable,__file__,'--serve',str(port)],env=env)
        processes=[start()]
        def restart():
            if processes[0].wait()==76:
                processes.append(start())
        supervisor=threading.Thread(target=restart,daemon=True)
        supervisor.start()
        for _ in range(100):
            if (root/'ready').exists():break
            time.sleep(.05)
        token='autumn-'+secrets.token_hex(3)
        (work/'make_word.py').write_text("from docx import Document\nd=Document();d.add_paragraph("+repr(token)+");d.save('report.docx')",encoding='utf8')
        (work/'make_pdf.py').write_text("from docx import Document\nimport fitz\nd=fitz.open();d.new_page().insert_text((72,72),Document('report.docx').paragraphs[0].text);d.save('report.pdf')",encoding='utf8')
        # Verify contents under the same native sandbox identity that created the files.
        with (work/'make_pdf.py').open('a',encoding='utf8') as script:
            script.write("\nd.close()\nwith fitz.open('report.pdf') as saved:\n print('READBACK '+Document('report.docx').paragraphs[0].text+' '+saved[0].get_text().strip())\n")
        prompt=(f'在当前工作目录实际执行两个已有的脚本，生成 report.docx 和 report.pdf。第一步单独执行 "{ARTIFACT_PYTHON}" make_word.py，'
                f'收到结果后，第二步单独执行 "{ARTIFACT_PYTHON}" make_pdf.py。分别声明写入的文件。执行完成后核对并交付两个文件。')
        try:
            result=subprocess.run([shutil.which('codex.exe'),'exec','--json','--skip-git-repo-check','--sandbox','workspace-write',prompt],
                                  cwd=work,env=dict(os.environ,CODEX_HOME=str(home),BRIDGE_FIXTURE_KEY=key),
                                  capture_output=True,encoding='utf8',errors='replace',timeout=900)
        finally:
            processes[-1].terminate(); processes[-1].wait(); supervisor.join(timeout=3)
        events=[json.loads(s) for s in result.stdout.splitlines() if s.startswith('{')]
        thread=next(e['thread_id'] for e in events if e['type']=='thread.started')
        key=fingerprint(thread)
        binding=json.loads((gateway.STATE/'conversations'/(key+'.json')).read_text())
        try:
            for e in events:
                i=e.get('item',{})
                if e['type']=='item.completed':
                    print(i['type'],str(i.get('text',i.get('aggregated_output','')))[-330:],flush=True)
            assert result.returncode==0,(result.stdout[-1800:],result.stderr[-350:])
            assert any('READBACK '+token+' '+token in e.get('item',{}).get('aggregated_output','')
                       for e in events if e.get('type')=='item.completed' and e.get('item',{}).get('exit_code')==0)
            assert (root/'restarted').exists()
            state=json.loads((gateway.STATE/'steps'/(key+'.json')).read_text())
            assert state.get('verified') and state.get('changes'),state.keys()
            assert binding['tool_reuses']>=1
            print('PASS: Word, PDF, provider restart and verified delivery. Actual sends:',binding.get('recent_sends'),flush=True)
        finally:
            close_fixture(dict(binding,url=binding.get('url','https://gemini.google.com/app')))

def serve(port):
    work=Path(os.environ['BRIDGE_TEST_WORK'])
    normal=gateway.web_reply
    def interrupt(*args,**kwargs):
        marker=work.parent/'restarted'
        if (work/'report.docx').exists() and not (work/'report.pdf').exists() and not marker.exists():
            marker.touch()
            os._exit(76)
        return normal(*args,**kwargs)
    gateway.web_reply=interrupt
    with gateway.ThreadingHTTPServer(('127.0.0.1',port),gateway.Handler) as server:
        server.key=os.environ['BRIDGE_FIXTURE_KEY']
        (work.parent/'ready').touch()
        server.serve_forever()

if __name__=='__main__':
    if '--serve' in sys.argv: serve(int(sys.argv[-1]))
    else: main()
