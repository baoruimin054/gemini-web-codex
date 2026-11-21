"""Generate a portable Codex profile for this copy of the gateway."""
import json
from pathlib import Path
import sys

def profile():
    command=json.dumps(Path(sys.executable).as_posix())
    args=json.dumps([(Path(__file__).resolve().parent/'gateway.py').as_posix(),'credential'],ensure_ascii=False)
    return f'''model_provider = "gemini_web"
model = "gemini-web-4-hydrogen"
web_search = "disabled"
[sandbox_workspace_write]
writable_roots = [{json.dumps((Path.home()/'Desktop').as_posix())}]
[model_providers.gemini_web]
name = "Gemini Web"
base_url = "http://127.0.0.1:18473/v1"
model_catalog_url = "http://127.0.0.1:18473/codex/models"
wire_api = "responses"
supports_websockets = false
request_max_retries = 1
stream_max_retries = 1
stream_idle_timeout_ms = 360000
[model_providers.gemini_web.auth]
command = {command}
args = {args}
timeout_ms = 5000
'''

if __name__=='__main__':
    print(profile())
