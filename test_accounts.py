import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import gateway
from account_selection import check_startup_account, select_account


class AccountTests(unittest.TestCase):
    def test_startup_does_not_keep_stale_model_permissions_when_ego_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            cache = state / 'quota.json'
            cache.write_text('{"account":{"models":["4 Neon"]}}', encoding='utf8')
            with patch('account_selection.ego_cli', return_value=None):
                check_startup_account(state)
            self.assertFalse(cache.exists())

    def test_catalog_and_request_preflight_follow_selected_account(self):
        free = {'id': 'a' * 64, 'slot': 0, 'models': ['Auto']}
        pro = {'id': 'b' * 64, 'slot': 1, 'models': ['Auto', '4 Hydrogen', '4 Neon']}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'quota.json').write_text(json.dumps(
                {'account': free, 'accounts': [free, pro]}), encoding='utf8')
            conversations = state / 'conversations'
            conversations.mkdir()
            (conversations / (gateway.fingerprint('pro-thread') + '.json')).write_text(
                json.dumps({'accountId': pro['id']}), encoding='utf8')
            with (patch.object(gateway, 'STATE', state),
                  patch.object(gateway, 'web_reply') as reply,
                  gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler) as server):
                server.key = 'test-local-key'
                worker = threading.Thread(target=server.serve_forever)
                worker.start()

                def request(method, path, model=None, thread=None):
                    headers = {'Authorization': 'Bearer ' + server.key}
                    if thread:
                        headers['thread-id'] = thread
                    conn = http.client.HTTPConnection(*server.server_address, timeout=5)
                    try:
                        body = json.dumps({'model': model, 'stream': True}) if model else None
                        conn.request(method, path, body, headers)
                        response = conn.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        conn.close()

                try:
                    _, body = request('GET', '/codex/models')
                    self.assertEqual([item['slug'] for item in body['models']], ['gemini-web-auto'])
                    status, body = request('POST', '/v1/responses', 'gemini-web-4-neon', 'new-thread')
                    self.assertEqual(status, 400)
                    self.assertIn('切换账号', body['error']['message'])
                    select_account(state, free)
                    status, body = request('POST', '/v1/responses', 'gemini-web-auto', 'pro-thread')
                    self.assertEqual(status, 400)
                    self.assertIn('另一个 Gemini 账号', body['error']['message'])
                    reply.assert_not_called()
                    select_account(state, pro)
                    _, body = request('GET', '/codex/models')
                    self.assertEqual({item['slug'] for item in body['models']},
                                     {'gemini-web-4-hydrogen', 'gemini-web-4-neon', 'gemini-web-auto'})
                finally:
                    server.shutdown()
                    worker.join()
