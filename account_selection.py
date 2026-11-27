"""Read the current Gemini account selection and matched account capabilities."""
import json
import os
from pathlib import Path
import re
import subprocess

from ego_installation import ego_cli, ensure_ego_background


def refresh_command(browser, state, *, include_quota=True):
    module = (Path(__file__).resolve().parent / 'quota.mjs').as_uri()
    args = json.dumps([str(state / 'quota.json'), selected_account(state), True, include_quota])
    script = 'await (await import(' + json.dumps(module) + ')).collect(...' + args + ')'
    return [browser, 'nodejs', '-e', script]


def check_startup_account(state):
    """Refresh model permissions before a new desktop window loads its catalog."""
    try:
        browser = ego_cli()
        if not browser:
            raise FileNotFoundError('ego-browser')
        ensure_ego_background(browser)
        subprocess.run(refresh_command(browser, state, include_quota=False), timeout=32,
                       creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        return
    except (OSError, subprocess.SubprocessError):
        # Use the in-browser check before sending when startup could not verify the account.
        cache = state / 'quota.json'
        try:
            cache.unlink(missing_ok=True)
        except OSError:
            pass


def load_account_file(path):
    try:
        value = json.loads(path.read_text(encoding='utf8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def selected_account(state):
    data = load_account_file(state / 'account-selection.json')
    if (isinstance(data.get('id'), str) and re.fullmatch(r'[0-9a-f]{64}', data['id'])
            and type(data.get('slot')) is int and 0 <= data['slot'] < 100):
        return data
    return None


def select_account(state, account):
    target = state / 'account-selection.json'
    temporary = target.with_suffix('.new')
    temporary.write_text(json.dumps({key: account[key] for key in ('id', 'slot')}), encoding='utf8')
    os.replace(temporary, target)


def available_models(state):
    cache = load_account_file(state / 'quota.json')
    chosen = selected_account(state)
    record = next((account for account in cache.get('accounts', [])
                   if account.get('id') == chosen['id'] and account.get('slot') == chosen['slot']),
                  None) if chosen else cache.get('account')
    models = record.get('models') if record else None
    return models if isinstance(models, list) and models else None
