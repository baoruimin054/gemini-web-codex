import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import watchdog


class GatewayWatchdogTests(unittest.TestCase):
    def test_only_restarts_when_listener_is_gone(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            with patch.object(watchdog, 'start_gateway') as start:
                self.assertFalse(watchdog.ensure_gateway(1234, port))
                start.assert_not_called()
                listener.close()
                self.assertTrue(watchdog.ensure_gateway(1234, port))
                start.assert_called_once_with(1234)

    def test_finds_only_the_dedicated_main_process(self):
        class Result:
            stdout = json.dumps([
                {'ProcessId': 1, 'CommandLine': 'ChatGPT.exe --user-data-dir=C:\\other'},
                {'ProcessId': 2, 'CommandLine': f'ChatGPT.exe --user-data-dir="{watchdog.STATE / "desktop"}"'},
                {'ProcessId': 3, 'CommandLine': f'ChatGPT.exe --type=renderer --user-data-dir="{watchdog.STATE / "desktop"}"'},
            ])

        with patch.object(watchdog.subprocess, 'run', return_value=Result()):
            self.assertEqual(watchdog.find_desktop_pid(), 2)

    def test_missing_binding_is_cleared_without_losing_chat_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'conversations'
            folder.mkdir()
            missing = folder / 'missing.json'
            protected = folder / 'protected.json'
            value = {'name': 'Codex Gemini example', 'space': 4, 'targetId': 'test',
                     'url': 'https://gemini.google.com/app/example'}
            for file in (missing, protected):
                file.write_text(json.dumps(value), encoding='utf8')
            with patch.object(watchdog, 'STATE', root):
                self.assertTrue(watchdog.clear_absent_bindings({'protected.json'}))
            restored = json.loads(missing.read_text(encoding='utf8'))
            self.assertEqual(restored['url'], value['url'])
            self.assertNotIn('space', restored)
            self.assertEqual(json.loads(protected.read_text(encoding='utf8'))['space'], 4)


if __name__ == '__main__':
    unittest.main()
