"""End-to-end fixture: isolated client configuration and randomized local test data."""
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
import gateway
from web_profile import profile

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='gemini-codex-check-') as tmp:
        root=Path(tmp)
        work=root/'work'
        home=root/'client'
        work.mkdir()
        home.mkdir()
        gateway.STATE.mkdir(exist_ok=True)
        server=gateway.ThreadingHTTPServer(('127.0.0.1',gateway.PORT),gateway.Handler)
        server.key=secrets.token_urlsafe(30)
        gateway.SPACE=int(os.environ['GEMINI_TEST_SPACE'])
        # This bounded fixture gets a separate temporary credential and no user MCP configuration.
        cfg=profile().split('[model_providers.gemini_web.auth]')[0]+'env_key = "GEMINI_TEST_GATEWAY_KEY"\n'
        (home/'config.toml').write_text(cfg,encoding='utf8')
        env=dict(os.environ,CODEX_HOME=str(home),GEMINI_TEST_GATEWAY_KEY=server.key)
        value=secrets.token_hex(15)
        (work/'input.txt').write_text(value,encoding='ascii')
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            result=subprocess.run([shutil.which('codex.exe') or shutil.which('codex'),'exec','--skip-git-repo-check',
                '--ephemeral','--model','gemini-web-4-hydrogen','--sandbox','read-only','--json','Use the shell to read input.txt and include its exact contents in your final answer.'],
                cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf8',errors='replace',timeout=600)
            import json
            messages=[e['item']['text'] for line in result.stdout.splitlines() if line.startswith('{')
                      for e in [json.loads(line)] if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='agent_message']
            passed=any(value in message for message in messages)
            print('exit',result.returncode,'final_has_nonce',passed)
            print(result.stdout[-2400:],result.stderr[-1000:])
            if result.returncode or not passed:
                raise SystemExit(1)
        finally:
            server.shutdown()
            server.server_close()
