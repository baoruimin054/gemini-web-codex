"""Inject a stalled worker and verify restoration in the original Gemini tab and chat."""
import json
import secrets
import tempfile
from pathlib import Path
from unittest.mock import patch
import gateway
from live_test_space import close_fixture
from conversation import fingerprint


def check():
    with tempfile.TemporaryDirectory() as directory, patch.object(gateway,'STATE',Path(directory)):
        thread=secrets.token_hex(12)
        marker='sample-'+secrets.token_hex(4)
        req={'model':'gemini-web-4-hydrogen','input':[{'role':'user','content':'Remember '+marker+' and reply OK.'}]}
        item=gateway.web_reply(req,thread)
        record=Path(directory)/'conversations'/(fingerprint(thread)+'.json')
        before=json.loads(record.read_text())
        req['input'].extend([{'role':'assistant','content':item['content'][0]['text']},
                             {'role':'user','content':'Return the sample marker from my first message exactly.'}])
        native=gateway.browser_turn
        def inject(request,thread,progress,fresh=False):
            if not fresh:
                raise gateway.StalledConversation()
            return native(request,thread,progress,fresh=True)
        with patch.object(gateway,'browser_turn',inject):
            result=gateway.web_reply(req,thread)
        after=json.loads(record.read_text())
        assert marker in result['content'][0]['text']
        assert all(before[k]==after[k] for k in ('url','space','targetId'))
        assert len(after['history'])==3
        close_fixture(after)
        print('Recovered on the original Gemini URL, space and tab:',after['space'])

if __name__=='__main__':
    check()
