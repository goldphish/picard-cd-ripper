import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

import bridge

TOKEN = 'secret'


class FakeTagger:
    def __init__(self):
        self.calls = []

    def load_album(self, release, discid=None):
        self.calls.append(('load_album', release, discid))

    def add_paths(self, paths):
        self.calls.append(('add_paths', paths))


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tagger = FakeTagger()
        api = SimpleNamespace(tagger=self.tagger, global_config=SimpleNamespace(
            setting={'clear_existing_tags': False, 'cd_lookup_device': '/dev/sr0,x'}))
        self.srv = bridge.Bridge(bridge.Handlers(api, '1.2.3'), TOKEN, 0,
                                 lambda fn: fn(), host='127.0.0.1')
        self.srv.start()
        self.addCleanup(self.srv.stop)

    def call(self, method, path, body=None, token=TOKEN):
        req = urllib.request.Request(
            f'http://127.0.0.1:{self.srv.port}{path}', method=method,
            data=None if body is None else json.dumps(body).encode())
        if token is not None:
            req.add_header('Authorization', f'Bearer {token}')
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_auth(self):
        self.assertEqual(self.call('GET', '/status', token=None)[0], 401)
        self.assertEqual(self.call('GET', '/status', token='nope')[0], 401)

    def test_status(self):
        with mock.patch.dict('sys.modules', {'picard': SimpleNamespace(PICARD_VERSION_STR='3.0.0')}):
            code, out = self.call('GET', '/status')
        self.assertEqual(code, 200)
        self.assertEqual(out, {'picard_version': '3.0.0', 'plugin_version': '1.2.3',
                               'clear_existing_tags': False})

    def test_unknown_route(self):
        self.assertEqual(self.call('GET', '/nope')[0], 404)

    def test_load(self):
        with tempfile.TemporaryDirectory() as d:
            code, _ = self.call('POST', '/load', {'paths': [d], 'release': 'r1', 'discid': 'd1'})
            self.assertEqual(code, 200)
            self.assertEqual(self.tagger.calls, [('load_album', 'r1', 'd1'), ('add_paths', [d])])

    def test_load_without_release(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.call('POST', '/load', {'paths': [d]})[0], 200)
            self.assertEqual(self.tagger.calls, [('add_paths', [d])])

    def test_load_validates(self):
        self.assertEqual(self.call('POST', '/load', {'paths': ['rel/path']})[0], 400)
        self.assertEqual(self.call('POST', '/load', {})[0], 400)
        self.assertEqual(self.call('POST', '/load', {'paths': [os.path.join(
            tempfile.gettempdir(), 'does-not-exist-xyz')]})[0], 404)
        self.assertEqual(self.tagger.calls, [])

    def test_disc(self):
        disc = SimpleNamespace(id='D', mcn='M', toc_string='1 2 3 4 5',
                               tracks=2, isrcs={2: 'I2', 1: 'I1'},
                               read=lambda device: None)
        mod = SimpleNamespace(Disc=lambda: disc)
        with mock.patch.dict('sys.modules', {'picard.disc': mod, 'picard': SimpleNamespace()}):
            code, out = self.call('GET', '/disc')
        self.assertEqual(code, 200)
        self.assertEqual(out, {'discid': 'D', 'toc': '1 2 3 4 5', 'mcn': 'M', 'isrcs': ['I1', 'I2']})


if __name__ == '__main__':
    unittest.main()
