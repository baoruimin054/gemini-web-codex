"""Close only a single tab positively identified as belonging to this live test."""
import json
import subprocess
from ego_installation import ego_cli


def close_fixture(binding):
    check = """
const info = (await listTaskSpaces()).find(s => s.id === binding.space && s.name === binding.name);
if (!info) throw Error('Test space missing');
const task = info.ownership === 'user' ? await claimTaskSpace(info.id) : await taskSpace(info.id);
const tabs = await task.tabs();
if (tabs.length !== 1 || tabs[0].targetId !== binding.targetId || tabs[0].url !== binding.url)
  throw Error('Test tab identity changed');
if (!tabs[0].label) await task.adopt(tabs[0].page);
await task.finish({keep:[]});
"""
    identity = {k:binding[k] for k in ('space','name','targetId','url')}
    result = subprocess.run([ego_cli(),'nodejs','-e',
                             'const binding='+json.dumps(identity)+';'+check],
                            capture_output=True,timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf8','replace')[-500:])
