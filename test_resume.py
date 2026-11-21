"""Regression coverage for resume on an existing, long Gemini conversation."""
import json
import tempfile
import unittest
from pathlib import Path
from conversation import fingerprint, prepare


class ResumeTests(unittest.TestCase):
    def test_recovery_reuses_verified_prefix_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'conversations'
            folder.mkdir()
            thread = 'long-test'
            old = {'type':'function_call_output','output':'x'*150000}
            record = folder / (fingerprint(thread)+'.json')
            record.write_text(json.dumps({'url':'https://gemini.google.com/app/abc',
                                          'history':[fingerprint(old)]}))
            latest = {'role':'user','content':'continue'}
            sent, _, _, binding, hashes = prepare({'input':[old,latest]},root,root,thread,fresh=True)
            self.assertEqual(sent,[latest])
            self.assertEqual(len(hashes),2)
            self.assertEqual(binding['url'],'https://gemini.google.com/app/abc')
            changed = {'role':'user','content':'different history'}
            sent, *_ = prepare({'input':[changed,latest]},root,root,thread,fresh=True)
            self.assertEqual(sent,[changed,latest])
