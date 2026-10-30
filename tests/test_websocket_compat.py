import unittest
from types import SimpleNamespace
from websockets.datastructures import Headers
from websocket_compat import normalize_close_upgrade


class TestWebSocketCompatibility(unittest.TestCase):
    def test_exact_known_proxy_defect_only(self):
        response=SimpleNamespace(status_code=101,headers=Headers({'Connection':'close','Upgrade':'websocket','Sec-WebSocket-Accept':'untouched'}))
        self.assertTrue(normalize_close_upgrade(response))
        self.assertEqual(response.headers['Connection'],'Upgrade')
        self.assertEqual(response.headers['Sec-WebSocket-Accept'],'untouched')

    def test_other_response_shapes_are_not_relaxed(self):
        for status,connection,upgrade in [(200,'close','websocket'),(101,'keep-alive','websocket'),
                                          (101,'close','not-websocket'),(101,'Upgrade','websocket')]:
            with self.subTest(status=status,connection=connection,upgrade=upgrade):
                response=SimpleNamespace(status_code=status,headers=Headers({'Connection':connection,'Upgrade':upgrade}))
                before=list(response.headers.raw_items())
                self.assertFalse(normalize_close_upgrade(response))
                self.assertEqual(list(response.headers.raw_items()),before)
