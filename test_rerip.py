import importlib
import json
import os
import random
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
        self.assertEqual(rerip.cdparanoia_args('--batch 1:-', 3), ['-e', '--batch', '--', '3'])
        self.assertEqual(rerip.cdparanoia_args('-B -z 1-', 12), ['-e', '-B', '-z', '--', '12'])
        self.assertEqual(rerip.cdparanoia_args('', 1), ['-e', '--batch', '--', '1'])
        # A value, not a span: kept.
        self.assertEqual(rerip.cdparanoia_args('-B -O 6', 2), ['-e', '-B', '-O', '6', '--', '2'])
        self.assertEqual(rerip.cdparanoia_args('-e -d /dev/sr1 -B', 2), ['-e', '-d', '/dev/sr1', '-B', '--', '2'])

    def test_sample_offset(self):
        self.assertEqual(rerip.cdparanoia_args('--batch 1:-', 3, -42), ['-e', '--batch', '-O', '-42', '--', '3'])
        self.assertEqual(rerip.with_sample_offset(['-B', '-O', '6'], -42), ['-B', '-O', '-36'])
        self.assertEqual(rerip.with_sample_offset(['--sample-offset=6'], 4), ['--sample-offset=10'])
        self.assertEqual(rerip.with_sample_offset(['-O6'], 4), ['-O10'])
        self.assertEqual(rerip.with_sample_offset(['-O', '6'], 0), ['-O', '6'])

    def test_line_up_args(self):
        self.assertEqual(rerip.line_up_args('-d /dev/sr1 -w --batch 1:-', 3, 4500, '/w/l.raw', -42),
                         ['-d', '/dev/sr1', '-O', '-42', '-r', '--', '3[1:00.00]-3[1:04.74]', '/w/l.raw'])
        self.assertIsNone(rerip.line_up_start(300 * 588))
        self.assertEqual(rerip.line_up_start(1375 * 588), 500)

    def test_alignment(self):
        rng = random.Random(1)
        track = rng.randbytes(1000 * 588 * 4)
        start = 300 * 588
        for d in (0, 42, -42, -5000):
            read = track[(start + d) * 4:(start + d + 375 * 588) * 4]
            self.assertEqual(rerip.alignment(read, start, track), d)
        # Damage in a few places doesn't hide the rest.
        read = bytearray(track[(start + 42) * 4:(start + 42 + 375 * 588) * 4])
        for at in (20000, 300000):
            read[at:at + 4000] = bytes(4000)
        self.assertEqual(rerip.alignment(bytes(read), start, track), 42)
        self.assertIsNone(rerip.alignment(bytes(375 * 588 * 4), start, track))
        self.assertIsNone(rerip.alignment(rng.randbytes(375 * 588 * 4), start, track))

    def test_paranoia_report(self):
        # Tracks start at sectors 0, 250 and 600; positions are in words.
        r = rerip.ParanoiaReport(rerip.toc_starts('1 3 1000 150 400 750'))
        shown = r.feed('Ripping from sector 0\n##: 0 [read] @ 1176\n##: 6 [sk')
        shown += r.feed(f'ip] @ {300 * 1176}\n##: 6 [skip] @ {301 * 1176}\n##: 5 [scratch repair] @ {700 * 1176}\nDone.')
        shown += r.flush()
        self.assertEqual(shown, 'Ripping from sector 0\nDone.')
        self.assertEqual(dict(r.track(2)), {'skip': 2})
        self.assertEqual(rerip.paranoia_summary(r.track(2)), '2 skips')
        self.assertEqual(rerip.paranoia_summary(r.track(3)), '1 scratch repair')
        self.assertEqual(rerip.paranoia_summary(r.track(1)), 'no problems')

    def test_describe_ctdb_track(self):
        self.assertEqual(rerip.describe({'verified': True, 'confidence': 7, 'source': 'ctdb'}),
                         'verified by the CUETools DB (confidence 7)')

    def test_verifier_args(self):
        self.assertEqual(rerip.verifier_args('--offset 6', '1 2 500 150 300', '/v'),
                         ['--offset', '6', '--json', '--force', '--toc', '1 2 500 150 300', '/v'])
        self.assertEqual(rerip.verifier_args('', '', '/v'), ['--json', '--force', '/v'])
        self.assertEqual(rerip.verifier_args('', '', '/v', '/music/a'),
                         ['--json', '--force', '--album', '/music/a', '/v'])

    def test_album_dir(self):
        same = [rerip.Track(1, 'A', '/music/a/01.flac'), rerip.Track(2, 'B', '/music/a/02.flac')]
        self.assertEqual(rerip.album_dir(same), '/music/a')
        split = same + [rerip.Track(3, 'C', '/music/b/03.flac')]
        self.assertEqual(rerip.album_dir(split), '')

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

    def test_edge_shift(self):
        toc = '1 3 1950 150 750 1350'
        self.assertEqual(rerip.edge_shift(toc, 1, -42), -42)
        self.assertEqual(rerip.edge_shift(toc, 2, -42), 0)
        self.assertEqual(rerip.edge_shift(toc, 3, -42), 0)
        self.assertEqual(rerip.edge_shift(toc, 1, 42), 0)
        self.assertEqual(rerip.edge_shift(toc, 3, 42), 42)
        # A hidden track before track 1 leaves room to read before it.
        self.assertEqual(rerip.edge_shift('1 3 1950 300 750 1350', 1, -42), 0)
        self.assertEqual(rerip.edge_shift('', 1, -42), 0)

    def test_shift_wav(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, 't.wav')
        for shift, want in ((-2, [0, 0, 1, 2, 3]), (2, [3, 4, 5, 0, 0])):
            with wave.open(path, 'wb') as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(b''.join(struct.pack('<hh', i, i) for i in (1, 2, 3, 4, 5)))
            rerip.shift_wav(path, shift)
            with wave.open(path, 'rb') as w:
                got = struct.unpack('<10h', w.readframes(w.getnframes()))
            self.assertEqual(list(got[::2]), want)

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


# Writes the same audio for a track every time, and reports events as
# cdparanoia -e does, with a skip on the tracks listed in skips.
FAKE_CDPARANOIA = '''#!/usr/bin/env python3
import json, os, struct, sys, wave
cfg = json.load(open(os.environ['FAKE_VERIFIER_CONFIG']))
track = int(sys.argv[-1])
assert sys.argv[-2] == '--' and '--batch' in sys.argv and '-e' in sys.argv, sys.argv
for i in range(50):
    sys.stderr.write('##: 0 [read] @ %d\\n' % (i * 1176))
if track in cfg.get('skips', []):
    sys.stderr.write('##: 6 [skip] @ 23520\\n')
sys.stderr.write('##: 7 [jitter] @ 23520\\n')
print('outputting to track%02d.cdda.wav' % track)
with wave.open('track%02d.cdda.wav' % track, 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
    w.writeframes(b''.join(struct.pack('<hh', i % 1000, i % 1000) for i in range(10 * 588)))
'''

# A drive with a disc in it, given as raw audio with its tracks' starts.
# -O reads later samples the higher it is, or earlier ones if the config
# says its sign is inverted. Rips a track as batch mode does, or a span of
# one as raw samples to a file.
FAKE_DISC_CDPARANOIA = '''#!/usr/bin/env python3
import json, os, re, sys, wave
cfg = json.load(open(os.environ['FAKE_VERIFIER_CONFIG']))
with open(os.environ['FAKE_VERIFIER_CONFIG'] + '.cdparanoia', 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
disc = open(cfg['disc'], 'rb').read()
args = sys.argv[1:]
shift = int(args[args.index('-O') + 1]) if '-O' in args else 0
if cfg.get('inverted'):
    shift = -shift
starts = cfg['starts']
def audio(begin, end):
    if begin + shift < 0 or (end + shift) * 4 > len(disc):
        sys.stderr.write('Error parsing span argument\\n')
        sys.exit(1)
    return disc[(begin + shift) * 4:(end + shift) * 4]
if '-r' in args:
    m = re.fullmatch(r'(\\d+)\\[(\\d+):(\\d+)\\.(\\d+)\\]-(\\d+)\\[(\\d+):(\\d+)\\.(\\d+)\\]', args[-2])
    n, m1, s1, f1, _, m2, s2, f2 = map(int, m.groups())
    a = starts[n - 1] + ((m1 * 60 + s1) * 75 + f1) * 588
    b = starts[n - 1] + ((m2 * 60 + s2) * 75 + f2 + 1) * 588
    open(args[-1], 'wb').write(audio(a, b))
else:
    n = int(args[-1])
    with wave.open('track%02d.cdda.wav' % n, 'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(audio(starts[n - 1], starts[n]))
'''

# Prints the result accuraterip-verify would: a track verifies unless it is
# listed in fail (the original file) or fail_new (a new rip).
FAKE_VERIFIER = '''#!/usr/bin/env python3
import json, os, sys
cfg = json.load(open(os.environ['FAKE_VERIFIER_CONFIG']))
assert '--force' in sys.argv and '--toc' in sys.argv, sys.argv
with open(os.environ['FAKE_VERIFIER_CONFIG'] + '.argv', 'w') as f:
    json.dump(sys.argv[1:], f)
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

    def _dialog(self, fail, fail_new, preselect=None, skips=()):
        with open(self.cfg_path, 'w') as f:
            json.dump({'fail': fail, 'fail_new': fail_new, 'skips': list(skips)}, f)
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
        # The verifier is told which album the links stand in for, so the
        # log goes in its directory rather than among the links.
        with open(self.cfg_path + '.argv') as f:
            argv = json.load(f)
        self.assertEqual(argv[argv.index('--album') + 1], os.path.dirname(self.tracks[0].path))
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

    def _rerip(self, d, n):
        d._tick([n])
        d.rerip_selected()
        self._wait(d)

    def test_rerip_keeps_the_original_when_the_new_rip_fails(self):
        d = self._dialog(fail=[2], fail_new=[2])
        d.start()
        self._wait(d)
        self._rerip(d, 2)
        self.assertIn('still does not verify; original kept. Re-rip again', self._result(d, 2))
        self.assertEqual(read(self.tracks[1].path), self.before[1])
        self.assertFalse(d.keep_button.isVisible())
        self.assertEqual(d.consistent, {})

    def test_keep_a_consistent_rerip(self):
        # Two re-rips read the same audio without a skip: they can be kept,
        # unverified, in place of a file that read differently.
        d = self._dialog(fail=[2], fail_new=[2])
        d.show()
        d.start()
        self._wait(d)
        self._rerip(d, 2)
        self._rerip(d, 2)
        self.assertIn('read the same audio twice with no skips', self._result(d, 2))
        self.assertTrue(d.keep_button.isVisible())
        self.assertEqual(read(self.tracks[1].path), self.before[1])

        d.keep_consistent()
        self.assertIn('kept the consistent re-rip', self._result(d, 2))
        self.assertIn('not verified', self._result(d, 2))
        self.assertNotEqual(read(self.tracks[1].path), self.before[1])
        self.assertEqual(mutagen.flac.FLAC(self.tracks[1].path)['title'], ['Song 2'])
        self.assertFalse(d.keep_button.isVisible())
        for i in (0, 2):
            self.assertEqual(read(self.tracks[i].path), self.before[i])

    def test_no_keeping_after_a_skip(self):
        # The same audio twice, but cdparanoia gave up on part of it: it may
        # have filled the gap the same way both times.
        d = self._dialog(fail=[2], fail_new=[2], skips=[2])
        d.show()
        d.start()
        self._wait(d)
        self._rerip(d, 2)
        self._rerip(d, 2)
        self.assertIn('cdparanoia skipped parts of it', self._result(d, 2))
        self.assertEqual(d.consistent, {})
        self.assertFalse(d.keep_button.isVisible())
        self.assertEqual(d.table.item(1, 4).text(), '1 skip, 1 jitter fix')

    def test_cdparanoia_events_are_counted_not_shown(self):
        d = self._dialog(fail=[2], fail_new=[], preselect=[2])
        d.start()
        self._rerip(d, 2)
        log = d.output.toPlainText()
        self.assertNotIn('##:', log)
        self.assertIn('outputting to track02.cdda.wav', log)
        self.assertIn('cdparanoia, track 2: 1 jitter fix', log)
        self.assertEqual(d.table.item(1, 4).text(), '1 jitter fix')
        self.assertEqual(d.table.item(0, 4).text(), '')

    def test_same_audio_as_the_file(self):
        # The re-rip reads exactly what the file holds: nothing to keep.
        d = self._dialog(fail=[2], fail_new=[2])
        d.start()
        self._wait(d)
        d._original_md5 = lambda n: d.attempts[n][-1].md5 if d.attempts.get(n) else b''
        self._rerip(d, 2)
        self.assertIn('read exactly the same audio as the current file', self._result(d, 2))
        self.assertEqual(d.consistent, {})

    def test_preselected_tracks_skip_the_first_verify(self):
        # cdcheckd already verified the rip and names the failing tracks.
        d = self._dialog(fail=[3], fail_new=[], preselect=[3])
        d.start()
        self.assertIsNone(d._process)
        self.assertEqual(self._ticked(d), [3])
        d.rerip_selected()
        self._wait(d)
        self.assertIn('file replaced', self._result(d, 3))

    def _disc(self, files_shift, pad=20 * 588):
        """Put a disc in the fake drive whose files are files_shift samples
        later than the drive reads it, as if ripped at another offset. The
        drive can read pad samples before the first track and after the last;
        the files have silence where it can't."""
        self.cdparanoia = self._script(os.path.join(self.dir, 'bin'), 'cdparanoia', FAKE_DISC_CDPARANOIA)
        sectors = 420
        rng = random.Random(2)
        disc = rng.randbytes((pad * 2 + 3 * sectors * 588) * 4)
        starts = [pad + i * sectors * 588 for i in range(4)]
        disc_path = os.path.join(self.dir, 'disc.raw')
        with open(disc_path, 'wb') as f:
            f.write(disc)
        for t, a, b in zip(self.tracks, starts, starts[1:]):
            wav = os.path.join(self.dir, 'in.wav')
            with wave.open(wav, 'wb') as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                edge = max(0, files_shift - a)
                w.writeframes(bytes(edge * 4) + disc[(a - files_shift + edge) * 4:(b - files_shift) * 4])
            encode(wav, t.path, TITLE=t.title, TRACKNUMBER=str(t.number))
        self.before = [read(t.path) for t in self.tracks]
        return {'disc': disc_path, 'starts': starts}

    def _disc_dialog(self, disc, **cfg):
        d = self._dialog(fail=[2], fail_new=[])
        with open(self.cfg_path) as f:
            c = json.load(f)
        c.update(disc, **cfg)
        with open(self.cfg_path, 'w') as f:
            json.dump(c, f)
        self.mod.CDPARANOIA = self.cdparanoia
        return d

    def test_rerips_line_up_with_files_ripped_at_another_offset(self):
        disc = self._disc(files_shift=42)
        d = self._disc_dialog(disc)
        d.start()
        self._wait(d)
        before = rerip.decode('flac', self.tracks[1].path)
        self._rerip(d, 2)
        self.assertEqual(d.shift, -42, d.output.toPlainText())
        self.assertIn('file replaced', self._result(d, 2))
        # The re-rip holds exactly the audio the file did: it fits.
        self.assertEqual(rerip.decode('flac', self.tracks[1].path), before)
        with open(self.cfg_path + '.cdparanoia') as f:
            runs = [json.loads(line) for line in f]
        self.assertEqual(len(runs), 3)  # a read, the check, the re-rip
        self.assertEqual(runs[-1][-4:], ['-O', '-42', '--', '2'])

        # Once known, the offset is used without reading again.
        self._rerip(d, 3)
        with open(self.cfg_path + '.cdparanoia') as f:
            self.assertEqual(len(f.readlines()), 4)

    def test_first_track_rerips_without_reading_before_the_disc(self):
        # With -42, cdparanoia would read before the first track's first
        # sample; it refuses that span, so the shift is made up afterwards.
        disc = self._disc(files_shift=42, pad=0)
        d = self._disc_dialog(disc)
        d.start()
        self._wait(d)
        before = rerip.decode('flac', self.tracks[0].path)
        self._rerip(d, 1)
        self.assertEqual(d.shift, -42, d.output.toPlainText())
        self.assertIn('file replaced', self._result(d, 1), d.output.toPlainText())
        self.assertEqual(rerip.decode('flac', self.tracks[0].path), before)
        with open(self.cfg_path + '.cdparanoia') as f:
            runs = [json.loads(line) for line in f]
        self.assertNotIn('-O', runs[-1])
        self.assertIn('read past the start of the disc', d.output.toPlainText())

    def test_files_in_line_need_no_shift(self):
        d = self._disc_dialog(self._disc(files_shift=0))
        d.start()
        self._wait(d)
        self._rerip(d, 2)
        self.assertEqual(d.shift, 0)
        with open(self.cfg_path + '.cdparanoia') as f:
            runs = [json.loads(line) for line in f]
        self.assertEqual(len(runs), 2)
        self.assertNotIn('-O', runs[-1])

    def test_nothing_rerips_when_the_offset_does_not_line_up(self):
        # cdparanoia shifts the other way from what's expected: the check
        # catches it before anything is re-ripped.
        d = self._disc_dialog(self._disc(files_shift=42), inverted=True)
        d.start()
        self._wait(d)
        self._rerip(d, 2)
        self.assertIn('still +84 samples out of line', d.status.text())
        self.assertIn('Nothing was re-ripped', d.status.text())
        self.assertIsNone(d.shift)
        self.assertEqual(read(self.tracks[1].path), self.before[1])

    def test_missing_verifier(self):
        d = self._dialog(fail=[], fail_new=[])
        d.config['verifier'] = os.path.join(self.dir, 'nope')
        d.start()
        self._wait(d)
        self.assertIn('Is it installed?', d.status.text())
        self.assertTrue(d.verify_button.isEnabled())


if __name__ == '__main__':
    unittest.main()
