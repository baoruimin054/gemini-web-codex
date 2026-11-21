import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ego_installation import ego_cli
from first_run import has_used_gemini


class FirstRunTests(unittest.TestCase):
    def test_ego_cli_detects_beta_in_an_alternate_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for version in ('0.6.2', '0.6.17-beta'):
                cli = root / version / 'ego-browser.exe'
                cli.parent.mkdir()
                cli.touch()
            with patch('ego_installation.ego_locations', return_value=[root]):
                self.assertEqual(ego_cli(), str(cli))

    def test_existing_successful_chat_migrates_without_a_login_prompt(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            folder = state / 'conversations'
            folder.mkdir()
            binding = folder / 'test.json'
            self.assertFalse(has_used_gemini(state))
            binding.write_text(json.dumps({'url': 'https://gemini.google.com/app/example'}), encoding='utf8')
            self.assertFalse(has_used_gemini(state))
            binding.write_text(json.dumps({'url': 'https://gemini.google.com/app/example',
                                          'history': ['verified']}), encoding='utf8')
            self.assertTrue(has_used_gemini(state))
