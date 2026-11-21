import http.client
import json
import threading
import unittest
from unittest.mock import patch

import gateway


class GatewayLifecycleTests(unittest.TestCase):
    def test_shutdown_requires_key_and_cancels_active_request(self):
        started = threading.Event()
        cancelled = threading.Event()

        def worker(request, thread, progress, stop):
            started.set()
            if stop.wait(8):
                cancelled.set()
            raise ConnectionResetError()

        with patch.object(gateway, 'web_reply', worker):
            with gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler) as server:
                server.key = 'test-only'
                service = threading.Thread(target=server.serve_forever, daemon=True)
                service.start()
                active = http.client.HTTPConnection(*server.server_address, timeout=5)
                control = http.client.HTTPConnection(*server.server_address, timeout=5)
                try:
                    control.request('POST', '/_control/shutdown', b'')
                    self.assertEqual(control.getresponse().status, 401)
                    control.close()
                    self.assertFalse(server.stopping.is_set())

                    body = json.dumps({'model': 'gemini-web-4-hydrogen', 'stream': True, 'input': 'test'})
                    active.request('POST', '/v1/responses', body,
                                   {'Authorization': 'Bearer test-only', 'thread-id': 'fixture'})
                    self.assertEqual(active.getresponse().status, 200)
                    self.assertTrue(started.wait(3))

                    control = http.client.HTTPConnection(*server.server_address, timeout=5)
                    control.request('POST', '/_control/shutdown', b'', {'Authorization': 'Bearer test-only'})
                    self.assertEqual(control.getresponse().status, 200)
                    self.assertTrue(cancelled.wait(5))
                    service.join(timeout=5)
                    self.assertFalse(service.is_alive())
                finally:
                    active.close()
                    control.close()
                    if service.is_alive():
                        server.shutdown()
                        service.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
