import importlib
import json
import os
import shutil
import stat
import struct
import sys
import tempfile
import types
import unittest
import wave

import rerip

HERE = os.path.dirname(os.path.abspath(__file__))
HAVE_FLAC = shutil.which('flac') is not None
try:
    import mutagen.flac
except ImportError:
    mutagen = None


def write_wav(path, seed, sectors=10):
    """A CD-audio WAV whose samples depend on seed."""
    with wave.open(path, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        n = sectors * 588
        w.writeframes(b''.join(struct.pack('<hh', (i * seed) % 30000, -(i * seed) % 30000)
                               for i in range(n)))


def read(path):
    with open(path, 'rb') as f:
        return f.read()


def encode(wav, out, **tags):
    import subprocess
    args = ['flac', '--silent', '-f', '-o', out]
    for k, v in tags.items():
        args += ['-T', f'{k}={v}']
    subprocess.run(args + [wav], check=True)


class ArgsTest(unittest.TestCase):
    def test_cdparanoia_args_replace_the_span(self):
        self.assertEqual(rerip.cdparanoia_args('--batch 1:-', 3), ['--batch', '--', '3'])
        self.assertEqual(rerip.cdparanoia_args('-B -z 1-', 12), ['-B', '-z', '--', '12'])
        self.assertEqual(rerip.cdparanoia_args('', 1), ['--batch', '--', '1'])
        # A value, not a span: kept.
        self.assertEqual(rerip.cdparanoia_args('-B -O 6', 2), ['-B', '-O', '6', '--', '2'])
        self.assertEqual(rerip.cdparanoia_args('-d /dev/sr1 -B', 2), ['-d', '/dev/sr1', '-B', '--', '2'])

    def test_verifier_args(self):
        self.assertEqual(rerip.verifier_args('--offset 6', '1 2 500 150 300', '/v'),
                         ['--offset', '6', '--json', '--force', '--toc', '1 2 500 150 300', '/v'])
        self.assertEqual(rerip.verifier_args('', '', '/v'), ['--json', '--force', '/v'])

    def test_parse_result(self):
        line = json.dumps({'status': 'MISS', 'tracks': [{'track': 1, 'verified': True},
                                                        {'track': 2, 'verified': False}]})
        r = rerip.parse_result('noise\n' + line + '\n')
        self.assertEqual(r['status'], 'MISS')
        self.assertEqual(rerip.failing(r), [2])
        err = rerip.parse_result('error: --toc has 3 tracks but /v has 2 files\n')
        self.assertEqual(err, {'status': 'ERR', 'message': 'error: --toc has 3 tracks but /v has 2 files'})

    def test_failing_needs_a_verified_track(self):
        # Nothing verifies: the pressing is missing, not the tracks bad.
        self.assertEqual(rerip.failing({'status': 'MISS', 'tracks': [
            {'track': 1, 'verified': False}, {'track': 2, 'verified': False}]}), [])
        self.assertEqual(rerip.failing({'status': 'NONE'}), [])

    def test_toc_track_count(self):
        self.assertEqual(rerip.toc_track_count('1 12 200000 150'), 12)
        self.assertIsNone(rerip.toc_track_count(''))


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def test_make_view(self):
        a, b = os.path.join(self.dir, 'a.flac'), os.path.join(self.dir, 'b.flac')
        new = os.path.join(self.dir, 'new.flac')
        view = os.path.join(self.dir, 'view')
        os.mkdir(view)
        tracks = [rerip.Track(1, 'A', a), rerip.Track(2, 'B', b)]
        rerip.make_view(tracks, {}, view)
        rerip.make_view(tracks, {2: new}, view)  # replaces the earlier links
        self.assertEqual(sorted(os.listdir(view)), ['01.flac', '02.flac'])
        self.assertEqual(os.readlink(os.path.join(view, '02.flac')), new)

    def test_replace_file(self):
        old, new = os.path.join(self.dir, 'old.flac'), os.path.join(self.dir, 'new.flac')
        with open(old, 'w') as f:
            f.write('old')
        os.chmod(old, 0o640)
        with open(new, 'w') as f:
            f.write('new')
        rerip.replace_file(new, old)
        with open(old) as f:
            self.assertEqual(f.read(), 'new')
        self.assertEqual(stat.S_IMODE(os.stat(old).st_mode), 0o640)
        self.assertEqual(sorted(os.listdir(self.dir)), ['new.flac', 'old.flac'])

    @unittest.skipUnless(HAVE_FLAC and mutagen, 'needs flac and mutagen')
    def test_copy_tags(self):
        wav = os.path.join(self.dir, 't.wav')
        old, new = os.path.join(self.dir, 'old.flac'), os.path.join(self.dir, 'new.flac')
        write_wav(wav, 3)
        encode(wav, old, TITLE='Song', REPLAYGAIN_ALBUM_GAIN='-7.00 dB')
        pic = mutagen.flac.Picture()
        pic.type, pic.mime, pic.data = 3, 'image/jpeg', b'jpeg'
        f = mutagen.flac.FLAC(old)
        f.add_picture(pic)
        f.save()
        encode(wav, new, REPLAYGAIN_ALBUM_GAIN='-1.00 dB', STRAY='x')
        rerip.copy_tags(old, new)
        got = mutagen.flac.FLAC(new)
        self.assertEqual(got['title'], ['Song'])
        self.assertEqual(got['replaygain_album_gain'], ['-7.00 dB'])
        self.assertNotIn('stray', got)
        self.assertEqual([p.data for p in got.pictures], [b'jpeg'])
        self.assertEqual(rerip.title_of(new), 'Song')


FAKE_CDPARANOIA = '''#!/usr/bin/env python3
import os, struct, sys, wave
track = int(sys.argv[-1])
assert sys.argv[-2] == '--' and '--batch' in sys.argv, sys.argv
with wave.open('track%02d.cdda.wav' % track, 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
    w.writeframes(b''.join(struct.pack('<hh', i % 1000, i % 1000) for i in range(10 * 588)))
'''

# Prints the result accuraterip-verify would: a track verifies unless it is
# listed in fail (the original file) or fail_new (a new rip).
FAKE_VERIFIER = '''#!/usr/bin/env python3
import json, os, sys
cfg = json.load(open(os.environ['FAKE_VERIFIER_CONFIG']))
assert '--force' in sys.argv and '--toc' in sys.argv, sys.argv
view = sys.argv[-1]
tracks = []
for i, name in enumerate(sorted(os.listdir(view)), 1):
    new = '/new/' in os.readlink(os.path.join(view, name))
    bad = i in (cfg['fail_new'] if new else cfg['fail'])
    tracks.append({'track': i, 'verified': not bad, 'confidence': 0 if bad else 5,
                   'crc_v1': 0xbad if bad else 0x900d})
ok = all(t['verified'] for t in tracks)
print(json.dumps({'dir': view, 'status': 'OK' if ok else 'MISS', 'tracks': tracks}))
sys.exit(0 if ok else 1)
'''


@unittest.skipUnless(HAVE_FLAC and mutagen, 'needs flac and mutagen')
class DialogTest(unittest.TestCase):
    """The Verify and re-rip window, with cdparanoia and the verifier faked."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        try:
            from PyQt6 import QtWidgets
        except ImportError as e:
            raise unittest.SkipTest(f'PyQt6: {e}')
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        # Import the dialog as part of the plugin package without running
        # its __init__, which needs Picard.
        pkg = types.ModuleType('cdripper')
        pkg.__path__ = [HERE]
        sys.modules['cdripper'] = pkg
        cls.mod = importlib.import_module('cdripper.rerip_dialog')

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        bin_dir = os.path.join(self.dir, 'bin')
        os.mkdir(bin_dir)
        self.cdparanoia = self._script(bin_dir, 'cdparanoia', FAKE_CDPARANOIA)
        self.verifier = self._script(bin_dir, 'verifier', FAKE_VERIFIER)
        self.cfg_path = os.path.join(self.dir, 'cfg.json')
        os.environ['FAKE_VERIFIER_CONFIG'] = self.cfg_path

        album = os.path.join(self.dir, 'album')
        os.mkdir(album)
        self.tracks = []
        for n in (1, 2, 3):
            wav = os.path.join(self.dir, 'in.wav')
            write_wav(wav, n)
            path = os.path.join(album, f'{n:02d} Song {n}.flac')
            encode(wav, path, TITLE=f'Song {n}', TRACKNUMBER=str(n), REPLAYGAIN_ALBUM_GAIN='-7.00 dB')
            self.tracks.append(self.mod.rerip.Track(n, f'Song {n}', path))
        self.before = [read(t.path) for t in self.tracks]

    def _script(self, d, name, text):
        path = os.path.join(d, name)
        with open(path, 'w') as f:
            f.write(text)
        os.chmod(path, 0o755)
        return path

    def _dialog(self, fail, fail_new, preselect=None):
        with open(self.cfg_path, 'w') as f:
            json.dump({'fail': fail, 'fail_new': fail_new}, f)
        config = {'cdparanoia_opts': '--batch 1:-', 'flac_opts': '--silent --verify --delete-input-file',
                  'verifier': self.verifier, 'verify_opts': '--offset 6'}
        self.mod.CDPARANOIA = self.cdparanoia
        d = self.mod.ReRipDialog(None, config, 'Album', self.tracks, '1 3 1950 150 750 1350', preselect)
        self.addCleanup(d.reject)
        return d

    def _wait(self, d):
        from PyQt6 import QtCore
        loop = QtCore.QEventLoop()
        timer = QtCore.QTimer()
        timer.timeout.connect(lambda: loop.quit() if d._process is None else None)
        timer.start(50)
        QtCore.QTimer.singleShot(30_000, loop.quit)
        loop.exec()
        timer.stop()
        self.assertIsNone(d._process, 'still running: ' + d.output.toPlainText())

    def _ticked(self, d):
        from PyQt6.QtCore import Qt
        return [t.number for row, t in enumerate(d.tracks)
                if d.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def _result(self, d, n):
        return d.table.item(n - 1, 3).text()

    def test_rerip_replaces_a_track_that_now_verifies(self):
        d = self._dialog(fail=[2], fail_new=[])
        d.start()
        self._wait(d)
        self.assertEqual(self._ticked(d), [2])
        self.assertIn('did not verify', self._result(d, 2))

        d.rerip_selected()
        self._wait(d)
        self.assertIn('file replaced', self._result(d, 2), d.output.toPlainText())
        self.assertEqual(self._ticked(d), [])
        self.assertNotEqual(read(self.tracks[1].path), self.before[1])
        for i in (0, 2):
            self.assertEqual(read(self.tracks[i].path), self.before[i])
        tags = mutagen.flac.FLAC(self.tracks[1].path)
        self.assertEqual(tags['title'], ['Song 2'])
        self.assertEqual(tags['replaygain_album_gain'], ['-7.00 dB'])

    def test_rerip_keeps_the_original_when_the_new_rip_fails(self):
        d = self._dialog(fail=[2], fail_new=[2])
        d.start()
        self._wait(d)
        d.rerip_selected()
        self._wait(d)
        self.assertIn('still does not verify, with the same CRC', self._result(d, 2))
        self.assertIn('original kept', self._result(d, 2))
        self.assertEqual(read(self.tracks[1].path), self.before[1])

    def test_preselected_tracks_skip_the_first_verify(self):
        # cdcheckd already verified the rip and names the failing tracks.
        d = self._dialog(fail=[3], fail_new=[], preselect=[3])
        d.start()
        self.assertIsNone(d._process)
        self.assertEqual(self._ticked(d), [3])
        d.rerip_selected()
        self._wait(d)
        self.assertIn('file replaced', self._result(d, 3))

    def test_missing_verifier(self):
        d = self._dialog(fail=[], fail_new=[])
        d.config['verifier'] = os.path.join(self.dir, 'nope')
        d.start()
        self._wait(d)
        self.assertIn('Is it installed?', d.status.text())
        self.assertTrue(d.verify_button.isEnabled())


if __name__ == '__main__':
    unittest.main()
