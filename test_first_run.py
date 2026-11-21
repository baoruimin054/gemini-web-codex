import json
from pathlib import Path
import tempfile
import unittest

from first_run import has_used_gemini


class FirstRunTests(unittest.TestCase):
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
