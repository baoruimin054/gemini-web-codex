"""Run against the installed gateway and a real Codex CLI in a temporary workspace."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
from web_profile import profile
from gateway import STATE
from conversation import fingerprint
from live_test_space import close_fixture


def check():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        home = root / 'client'
        home.mkdir()
        (home / 'config.toml').write_text(profile()+'\n[windows]\nsandbox = "elevated"\n', encoding='utf8')
        work = root / 'work'
        work.mkdir()
        target = secrets.token_hex(8) + '.txt'
        value = secrets.token_hex(16)
        (work / 'pointer.txt').write_text(target)
        (work / target).write_text(value)
        desktop_file=Path.home()/'Desktop'/('codex-gemini-test-'+secrets.token_hex(12)+'.txt')
        assert not desktop_file.exists()
        prompt = ('Use four separate shell calls in order. First read only pointer.txt. Wait for its result. '
                  'In a second shell call read the file named in that result. Third write that value to '
                  +str(desktop_file)+' . Fourth read back that desktop file and return its exact content.')
        result = subprocess.run([shutil.which('codex.exe'), 'exec', '--json', '--skip-git-repo-check',
                                 '--sandbox', 'workspace-write', '-c', 'model_reasoning_effort="high"', prompt],
                                cwd=work, env=dict(os.environ, CODEX_HOME=str(home)), capture_output=True,
                                encoding='utf8', errors='replace', timeout=850)
        events = [json.loads(s) for s in result.stdout.splitlines() if s.startswith('{')]
        items = [e['item'] for e in events if e['type']=='item.completed']
        calls = [i for i in items if i['type']=='command_execution' and i.get('exit_code')==0]
        try:
            assert result.returncode==0, (result.stdout+result.stderr)[-1300:]
            outputs=[call['aggregated_output'] for call in calls]
            first=next(i for i,text in enumerate(outputs) if target in text)
            second=next(i for i,text in enumerate(outputs) if i>first and value in text)
            assert any(value in text for text in outputs[second+2:]),items
            assert desktop_file.read_text(encoding='utf-8-sig').strip()==value
            assert any(i['type']=='agent_message' and value in i['text'] for i in items), items
        finally:
            desktop_file.unlink(missing_ok=True)
        thread=next(e['thread_id'] for e in events if e['type']=='thread.started')
        binding=json.loads((STATE/'conversations'/(fingerprint(thread)+'.json')).read_text())
        assert binding['url'] and binding['targetId']
        close_fixture(binding)
        print('Verified dependent reads, desktop write, readback and final answer. Test space', binding['space'])

if __name__=='__main__':
    check()
