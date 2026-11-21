"""Live smoke test against the local gateway. Requires an authenticated Gemini web session."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile

from PIL import Image

from conversation import fingerprint
from gateway import STATE
from web_profile import profile
from live_test_space import close_fixture


def check():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        home=root/'client'
        home.mkdir()
        (home/'config.toml').write_text(profile(),encoding='utf8')
        picture=root/'sample.png'
        Image.new('RGB',(96,96),(0,255,0)).save(picture)
        nonce=secrets.token_hex(5)
        def run(*args):
            result=subprocess.run([shutil.which('codex.exe'),'exec',*args],cwd=root,
                env=dict(os.environ,CODEX_HOME=str(home)),capture_output=True,encoding='utf8',errors='replace',timeout=350)
            events=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
            if result.returncode:
                raise RuntimeError((result.stderr+' '+result.stdout)[-1600:])
            print('events',[(e['type'],e.get('item',{}).get('type')) for e in events],flush=True)
            return events
        first=run('--skip-git-repo-check','--sandbox','read-only','--json','-i',str(picture),'--',
            'Remember '+nonce+'. Without tools, identify the single color in this picture in English.')
        assert any('green' in e.get('item',{}).get('text','').lower() for e in first)
        thread=next(e['thread_id'] for e in first if e['type']=='thread.started')
        record=STATE/'conversations'/(fingerprint(thread)+'.json')
        before=json.loads(record.read_text())
        second=run('resume','--skip-git-repo-check','--json',thread,'What was my secret? Reply with exactly that string, without tools.')
        after=json.loads(record.read_text())
        assert any(nonce in e.get('item',{}).get('text','') for e in second)
        assert before['url']==after['url'] and before['space']==after['space']
        assert before['targetId']==after['targetId']
        close_fixture(after)
        print('image and multi-turn checks passed; browser space',after['space'])

if __name__=='__main__':
    check()
