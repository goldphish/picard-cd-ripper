"""The rip's handling of a CD that was ripped before, with Picard stubbed."""
import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace

from test_rerip import HAVE_FLAC, encode, mutagen, write_wav

def md5(path):
    """The MD5 of a FLAC file's audio."""
    return mutagen.flac.FLAC(path).info.md5_signature


HERE = os.path.dirname(os.path.abspath(__file__))


def load_plugin():
    """Import the plugin package with stand-ins for Picard's modules."""
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    api = types.ModuleType('picard.plugin3.api')
    api.BaseAction, api.OptionsPage, api.PluginApi = object, QtWidgets.QWidget, object
    stubs = {'picard': types.ModuleType('picard'), 'picard.plugin3': types.ModuleType('picard.plugin3'),
             'picard.plugin3.api': api, 'picard.disc': SimpleNamespace(Disc=object),
             'picard.util': SimpleNamespace(sanitize_filename=lambda s: s)}
    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)
    spec = importlib.util.spec_from_file_location(
        'cdripper_plugin', os.path.join(HERE, '__init__.py'), submodule_search_locations=[HERE])
    plugin = importlib.util.module_from_spec(spec)
    sys.modules['cdripper_plugin'] = plugin
    spec.loader.exec_module(plugin)
    return plugin, app


class FakeTrack:
    def __init__(self, n):
        self.metadata = {'tracknumber': str(n), 'title': f'Song {n}'}
        self.files = []

    def iterfiles(self):
        return iter(self.files)


class FakeFile:
    def __init__(self, filename, targets):
        self.filename = filename
        self.targets = targets  # where saving would put each tmp file
        self.track = None

    def make_filename(self, filename, metadata):
        return self.targets.get(filename, filename)

    def move(self, track):
        if self.track is not None:
            self.track.files.remove(self)
        self.track = track
        track.files.append(self)

    def load(self, callback):
        pass


@unittest.skipUnless(HAVE_FLAC and mutagen, 'needs flac and mutagen')
class RipAgainTest(unittest.TestCase):
    """Ripping a CD again replaces the old files instead of adding to them."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.plugin, cls.app = load_plugin()
        except ImportError as e:
            raise unittest.SkipTest(f'PyQt6: {e}')

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.library = os.path.join(self.dir, 'library')
        self.tmp = os.path.join(self.dir, 'tmp')
        os.mkdir(self.library)
        os.mkdir(self.tmp)
        self.targets = {}
        self.tagger = SimpleNamespace(files={}, format_registry=SimpleNamespace(
            open=lambda path: FakeFile(path, self.targets)))
        ripper = self.plugin.CDRipper.__new__(self.plugin.CDRipper)
        ripper.tagger, ripper.log = self.tagger, SimpleNamespace(error=print, debug=lambda *_: None)
        ripper.widget = SimpleNamespace(ui=SimpleNamespace(
            encode_output=SimpleNamespace(appendPlainText=lambda _: None),
            finished_button=SimpleNamespace(setEnabled=lambda _: None)))
        ripper.album = SimpleNamespace(load=lambda: None)
        ripper._encode_process_count = 1
        self.ripper = ripper

    def flac(self, path, seed, **tags):
        wav = os.path.join(self.dir, 'in.wav')
        write_wav(wav, seed)
        encode(wav, path, **tags)

    def rip(self, *tracks):
        """Finish a rip of tracks, given as (new file, track)."""
        self.ripper._flac_files = list(tracks)
        self.ripper._expected_num_tracks = len(tracks)
        self.ripper._tmpdir = self.tmp
        self.ripper.encodeFinished()

    def test_replaces_the_file_on_the_track(self):
        old = os.path.join(self.library, '01 Song 1.flac')
        self.flac(old, 1, TITLE='Song 1', REPLAYGAIN_ALBUM_GAIN='-7.00 dB')
        track = FakeTrack(1)
        loaded = FakeFile(old, self.targets)
        self.tagger.files[old] = loaded
        loaded.move(track)
        new = os.path.join(self.tmp, '01 Song 1.flac')
        self.flac(new, 2)
        audio = md5(new)

        self.rip((new, track))
        self.assertEqual(track.files, [loaded])
        self.assertEqual(list(self.tagger.files), [old])
        self.assertFalse(os.path.exists(new))
        self.assertEqual(sorted(os.listdir(self.library)), ['01 Song 1.flac'])
        self.assertEqual(md5(old), audio)
        self.assertEqual(mutagen.flac.FLAC(old)['replaygain_album_gain'], ['-7.00 dB'])
        self.assertEqual(self.ripper._flac_files, [(old, track)])

    def test_replaces_the_file_saving_would_collide_with(self):
        # The old rip is not loaded, but it holds the name saving would use:
        # Picard would save the new one beside it as "01 Song 1 (1).flac".
        old = os.path.join(self.library, '01 Song 1.flac')
        self.flac(old, 1, TITLE='Song 1')
        track = FakeTrack(1)
        new = os.path.join(self.tmp, '01 Song 1.flac')
        self.flac(new, 2)
        audio = md5(new)
        self.targets[new] = old

        self.rip((new, track))
        self.assertEqual([f.filename for f in track.files], [old])
        self.assertEqual(list(self.tagger.files), [old])
        self.assertEqual(md5(old), audio)
        self.assertEqual(mutagen.flac.FLAC(old)['title'], ['Song 1'])
        self.assertEqual(sorted(os.listdir(self.library)), ['01 Song 1.flac'])

    def test_paranoia_lines(self):
        report = self.plugin.rerip.ParanoiaReport([0, 100])
        report.feed(f'##: 0 [read] @ 0\n##: 6 [skip] @ {150 * 1176}\n')
        self.assertEqual(self.plugin._paranoia_lines(report), 'cdparanoia, track 2: 1 skip')
        self.assertEqual(self.plugin._paranoia_lines(self.plugin.rerip.ParanoiaReport()),
                         'cdparanoia reported no problems.')

    def test_first_rip_attaches_the_new_file(self):
        track = FakeTrack(1)
        new = os.path.join(self.tmp, '01 Song 1.flac')
        self.flac(new, 2)
        self.targets[new] = os.path.join(self.library, '01 Song 1.flac')  # nothing there yet

        self.rip((new, track))
        self.assertEqual([f.filename for f in track.files], [new])
        self.assertTrue(os.path.exists(new))
        self.assertEqual(os.listdir(self.library), [])


if __name__ == '__main__':
    unittest.main()
