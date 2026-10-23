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


class FakeAlbum:
    def __init__(self, id):
        self.id = id
        self.files = []

    def iterfiles(self):
        return iter(self.files)


class FakeFile:
    def __init__(self, album):
        self.state = 'PENDING'
        self.parent_item = None
        self.album = album

    def place(self):
        """What Picard does once loaded: file it under its tagged release."""
        self.state, self.parent_item = 'NORMAL', self.album
        self.album.files.append(self)


class FakeTagger:
    def __init__(self):
        self.calls = []
        self.albums = {}
        self.files = {}
        self.tagged = None  # release the files' MUSICBRAINZ_ALBUMID names

    def load_album(self, release, discid=None):
        self.calls.append(('load_album', release, discid))
        return self.albums.setdefault(release, FakeAlbum(release))

    def add_paths(self, paths):
        self.calls.append(('add_paths', paths))
        if self.tagged:
            old = self.albums.setdefault(self.tagged, FakeAlbum(self.tagged))
            for i in range(2):
                self.files[os.path.join(paths[0], f'{i}.flac')] = FakeFile(old)

    def move_files_to_album(self, files, album):
        self.calls.append(('move', len(files), album.id))
        for f in files:
            f.album.files.remove(f)
            f.album = album
            album.files.append(f)

    def remove_album(self, album):
        self.calls.append(('remove_album', album.id))
        del self.albums[album.id]


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tagger = FakeTagger()
        self.scheduled = []
        api = SimpleNamespace(tagger=self.tagger, logger=mock.Mock(), global_config=SimpleNamespace(
            setting={'clear_existing_tags': False, 'cd_lookup_device': '/dev/sr0,x'}))
        handlers = bridge.Handlers(api, '1.2.3', schedule=lambda ms, fn: self.scheduled.append(fn))
        self.srv = bridge.Bridge(handlers, TOKEN, 0, lambda fn: fn(), host='127.0.0.1')
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

    def test_load_moves_files_to_requested_release(self):
        # The files are tagged with another edition: Picard files them there,
        # without the disc ID. The bridge moves them to the requested release.
        self.tagger.tagged = 'old'
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.call('POST', '/load', {'paths': [d], 'release': 'new', 'discid': 'd1'})[0], 200)
            self.assertEqual(len(self.scheduled), 1)  # still loading: check again later
            for f in self.tagger.files.values():
                f.place()
            self.scheduled.pop()()
            self.assertEqual(self.tagger.calls[-2:], [('move', 2, 'new'), ('remove_album', 'old')])
            self.assertEqual(len(self.tagger.albums['new'].files), 2)
            self.assertEqual(self.scheduled, [])

    def test_load_leaves_files_already_on_release(self):
        self.tagger.tagged = 'r1'
        with tempfile.TemporaryDirectory() as d:
            self.call('POST', '/load', {'paths': [d], 'release': 'r1', 'discid': 'd1'})
            for f in self.tagger.files.values():
                f.place()
            self.scheduled.pop()()
            self.assertNotIn('move', [c[0] for c in self.tagger.calls])

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
                               tracks=[SimpleNamespace(isrc='I1'), SimpleNamespace(isrc=None)],
                               read=lambda device: None)
        mod = SimpleNamespace(Disc=lambda: disc)
        with mock.patch.dict('sys.modules', {'picard.disc': mod, 'picard': SimpleNamespace()}):
            code, out = self.call('GET', '/disc')
        self.assertEqual(code, 200)
        self.assertEqual(out, {'discid': 'D', 'toc': '1 2 3 4 5', 'mcn': 'M', 'isrcs': ['I1']})


if __name__ == '__main__':
    unittest.main()
