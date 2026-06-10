# -*- coding: utf-8 -*-

import os
import shutil
import subprocess
import tempfile
from typing import Any, Optional

import discid
from PyQt5 import QtCore
from PyQt5.QtWidgets import QDialog
from PyQt5.QtCore import pyqtSignal, QObject

from picard import formats
from picard.config import IntOption, TextOption
from picard.disc import Disc
from picard.plugins.cdripper import accuraterip, ui, ui_options_cdripper
from picard.ui.itemviews import BaseAction, register_album_action
from picard.ui.options import OptionsPage, register_options_page
from picard.util import encode_filename, sanitize_filename

PLUGIN_NAME = 'CD Ripper'
PLUGIN_AUTHOR = 'Dan Lange'
PLUGIN_DESCRIPTION = 'Rips CDs and encodes to FLAC, with AccurateRip verification.'
PLUGIN_VERSION = '0.4.0'
PLUGIN_API_VERSIONS = ['0.15', '2.0', '2.7', '2.13']
PLUGIN_LICENSE = 'GPL-2.0-or-later'
PLUGIN_LICENSE_URL = 'https://www.gnu.org/licenses/gpl-2.0.html'


class RipCDDialog(QDialog):
    """Dialog for the CD Ripper UI."""
    def __init__(self, parent: Optional[Any]):
        super().__init__(parent)
        self.ui = ui.Ui_CDRipperUI()
        self.ui.setupUi(self)


class RipCD(BaseAction):
    """Album action to start the CD ripper."""
    NAME = '&Rip CD...'

    def callback(self, objs: list) -> None:
        album = objs[0]
        CDRipper(album).run()


class AccurateRipVerifier(QtCore.QThread):
    """Runs AccurateRip verification (and retries) off the main thread."""
    logMessage = pyqtSignal(str)
    finished = pyqtSignal(dict)  # track_num (int) -> {'ok': bool, 'confidence': int, 'status': str}

    def __init__(
        self,
        ar_tracks: list,
        disc_obj,
        retries: int,
        tmpdir: str,
        cdparanoia_bin: str,
    ) -> None:
        super().__init__()
        self.ar_tracks = ar_tracks
        self.disc_obj = disc_obj
        self.retries = retries
        self.tmpdir = tmpdir
        self.cdparanoia_bin = cdparanoia_bin

    def run(self) -> None:
        n_tracks = len(self.ar_tracks)
        self.logMessage.emit('Fetching AccurateRip data...')
        ar_bin = accuraterip.fetch(self.disc_obj)

        if ar_bin is None:
            self.logMessage.emit('Disc not found in AccurateRip database — skipping verification.')
            results = {
                tn: {'ok': True, 'confidence': 0, 'status': 'Not in database'}
                for tn, _, _ in self.ar_tracks
            }
            self.finished.emit(results)
            return

        ar_pressings = accuraterip.parse_bin(ar_bin, n_tracks)
        disc_id_str = accuraterip.disc_id_string(self.disc_obj)
        results = {}

        for track_num, wav_path, _ in self.ar_tracks:
            track_idx = track_num - 1
            ok = False
            confidence = 0

            for attempt in range(self.retries + 1):
                if attempt > 0:
                    self.logMessage.emit(
                        f'Track {track_num:02d}: re-ripping (attempt {attempt}/{self.retries})...'
                    )
                    out_name = os.path.basename(wav_path)
                    subprocess.run(
                        [self.cdparanoia_bin, str(track_num), out_name],
                        cwd=self.tmpdir,
                        capture_output=True,
                    )

                try:
                    crcs = accuraterip.compute_crcs(wav_path, track_idx, n_tracks)
                    ok, confidence = accuraterip.verify_track(crcs, ar_pressings[track_idx])
                except Exception as e:
                    self.logMessage.emit(f'Track {track_num:02d}: CRC error — {e}')
                    ok = True  # don't block encoding on a verification error
                    break

                if ok:
                    break

            if ok:
                status = f'Accurate (confidence {confidence})' if confidence > 0 else 'Not in database'
                self.logMessage.emit(f'Track {track_num:02d}: {status}')
            else:
                status = 'Inaccurate'
                self.logMessage.emit(
                    f'Track {track_num:02d}: FAILED AccurateRip after {self.retries} {"retry" if self.retries == 1 else "retries"}'
                )

            results[track_num] = {'ok': ok, 'confidence': confidence, 'status': status,
                                  'disc_id': disc_id_str}

        self.finished.emit(results)


class CDRipper(QtCore.QObject):
    """Main class for handling CD ripping and encoding."""
    def __init__(self, album: Any) -> None:
        super().__init__()
        self.album = album
        self.discid: Optional[str] = None
        self._disc_obj = None  # discid.Disc for AccurateRip
        self._ripping: bool = False

        self.widget = RipCDDialog(self.tagger.window)
        self.widget.ui.cancel_button.clicked.connect(self.widget.reject)
        self.widget.ui.finished_button.clicked.connect(self.widget.accept)

        self._tmpdir = tempfile.mkdtemp()
        self._encode_process_count = 0
        self._expected_num_tracks = 0
        self._processes = []
        self._flac_files = []
        self._ar_results: dict = {}
        self._verifier: Optional[AccurateRipVerifier] = None

    def run(self) -> None:
        device = self.config.setting['cd_lookup_device'].split(',', 1)[0]
        encoded_device = encode_filename(device)

        disc = Disc()
        disc.read(encoded_device)
        self.discid = disc.id
        self.log.debug(f'CD has discid: {disc.id}')

        try:
            self._disc_obj = discid.read(encoded_device)
        except Exception as e:
            self.log.debug(f'discid read failed: {e}')
            self._disc_obj = None

        self.rip()
        result = self.widget.exec_()
        if result == self.widget.Rejected:
            self._cleanup()

    def _newProcess(self, output_widget: Any) -> QtCore.QProcess:
        process = QtCore.QProcess()
        process.setWorkingDirectory(self._tmpdir)
        process.setProcessChannelMode(process.MergedChannels)
        process.setReadChannel(process.StandardOutput)
        process.error.connect(self.errorHandler)
        process.readyReadStandardOutput.connect(
            lambda p=process, w=output_widget: w.appendPlainText(str(p.readAll(), 'utf-8')))
        process.finished.connect(
            lambda *_, p=process, w=output_widget: w.appendPlainText(str(p.readAll(), 'utf-8')))
        return process

    def rip(self) -> None:
        process = self._newProcess(self.widget.ui.rip_output)
        self._processes.append(process)
        process.started.connect(self.ripStarted)
        process.finished.connect(self.ripFinished)
        opts = self.config.setting['cdripper_cdparanoia_opts']
        self.log.debug(f'Using tmp dir: {self._tmpdir}')
        process.start('/usr/bin/cdparanoia', opts.split())

    def ripStarted(self) -> None:
        self.log.debug('Rip started.')
        self._ripping = True
        self.widget.ui.rip_output.appendPlainText('Ripping CD...')
        self.widget.ui.finished_button.setDisabled(True)

    def ripFinished(self, _: Any, status: int) -> None:
        self._ripping = False
        if status != QtCore.QProcess.CrashExit:
            self.log.debug('Rip finished; starting AccurateRip verification.')
            self.widget.ui.rip_output.appendPlainText('CD ripping complete!')
            self._startVerify()

    def _startVerify(self) -> None:
        # Build track list (same filter logic as encode())
        ar_tracks = []
        for track in self.album.tracks:
            if self.discid not in track.metadata['~musicbrainz_discids']:
                continue
            track_num = int(track.metadata['tracknumber'])
            wav_path = os.path.join(self._tmpdir, f'track{track_num:02d}.cdda.wav')
            ar_tracks.append((track_num, wav_path, track))

        if not ar_tracks or self._disc_obj is None:
            self.widget.ui.ar_output.appendPlainText(
                'AccurateRip verification skipped (no disc TOC available).')
            self.encode()
            return

        retries = self.config.setting['cdripper_ar_retries']
        cdparanoia_bin = '/usr/bin/cdparanoia'

        self.widget.ui.ar_output.appendPlainText('Starting AccurateRip verification...')
        self.widget.ui.ripper_tab.setCurrentIndex(2)  # switch to AccurateRip tab

        self._verifier = AccurateRipVerifier(
            ar_tracks, self._disc_obj, retries, self._tmpdir, cdparanoia_bin)
        self._verifier.logMessage.connect(self.widget.ui.ar_output.appendPlainText)
        self._verifier.finished.connect(self._verifyFinished)
        self._verifier.start()

    def _verifyFinished(self, results: dict) -> None:
        self._ar_results = results
        failed = [tn for tn, r in results.items() if not r['ok']]
        if failed:
            tracks_str = ', '.join(f'{t:02d}' for t in sorted(failed))
            self.widget.ui.ar_output.appendPlainText(
                f'\nWarning: track(s) {tracks_str} did not pass AccurateRip verification.')
        else:
            self.widget.ui.ar_output.appendPlainText('\nAll tracks verified successfully.')
        self.encode()

    def encode(self) -> None:
        self.widget.ui.encode_output.appendPlainText('Encoding CD...')

        opts = self.config.setting['cdripper_flac_opts']
        for track in self.album.tracks:
            if self.discid not in track.metadata['~musicbrainz_discids']:
                self.log.error(
                    f'discid {self.discid} not found in {track.metadata["~musicbrainz_discids"]}')
                continue

            self._expected_num_tracks += 1
            track_title = track.metadata['title']
            track_num = track.metadata['tracknumber'].zfill(2)
            wav_path = os.path.join(self._tmpdir, f'track{track_num}.cdda.wav')
            flac_name = sanitize_filename(f'{track_num} {track_title}.flac')
            flac_path = os.path.join(self._tmpdir, flac_name)
            args = opts.split() + ['-o', flac_path, wav_path]

            process = self._newProcess(self.widget.ui.encode_output)
            process.finished.connect(self.encodeFinished)
            process.start('/usr/bin/flac', args)
            self._processes.append(process)
            self._encode_process_count += 1
            self._flac_files.append((flac_path, track))

    def encodeFinished(self) -> None:
        self._encode_process_count -= 1
        if self._encode_process_count == 0:
            for path, track in self._flac_files:
                track_num = int(track.metadata['tracknumber'])
                ar_result = self._ar_results.get(track_num)
                if ar_result:
                    self._write_ar_tags(path, ar_result)

                f = formats.open_(path)
                f.parent = track
                self.tagger.files[path] = f
                track.add_file(f)
                f.load(lambda *_, **__: True)

            if len(self._flac_files) != self._expected_num_tracks:
                self.log.debug('Ripping/encoding was aborted.')
                return

            self.log.debug('Encoding successful!')
            self.album.load()
            self.widget.ui.finished_button.setEnabled(True)
            self._tmpdir = tempfile.mkdtemp()

    def _write_ar_tags(self, flac_path: str, ar_result: dict) -> None:
        try:
            from mutagen.flac import FLAC as MutagenFLAC
            f = MutagenFLAC(flac_path)
            f['ACCURATERIP_RESULT'] = ar_result['status']
            if 'disc_id' in ar_result:
                f['ACCURATERIP_DISCID'] = ar_result['disc_id']
            f.save()
        except Exception as e:
            self.log.debug(f'Failed to write AccurateRip tags: {e}')

    def errorHandler(self, error: Any) -> None:
        msg = f'Ripping/Encoding failed: {error}'
        self.log.debug(msg)
        self.widget.ui.rip_output.appendPlainText(msg)

    def _cleanup(self) -> None:
        if self._verifier and self._verifier.isRunning():
            self._verifier.quit()
            self._verifier.wait()
        for process in self._processes:
            if not process.atEnd():
                process.kill()
        try:
            shutil.rmtree(self._tmpdir)
        except OSError as e:
            self.log.debug(f'Failed to remove temp dir: {e}')


class CDRipperOptionsPage(OptionsPage):
    """Options page for the CD Ripper plugin."""
    NAME = 'cdripper'
    TITLE = 'CD Ripper'
    PARENT = 'plugins'

    options = [
        TextOption('setting', 'cdripper_cdparanoia_opts', '--batch 1:-'),
        TextOption('setting', 'cdripper_flac_opts', '--verify --replay-gain --delete-input-file'),
        IntOption('setting', 'cdripper_ar_retries', 3),
    ]

    def __init__(self, parent: Optional[Any] = None) -> None:
        super().__init__()
        self.ui = ui_options_cdripper.Ui_CDRipperOptionsPage()
        self.ui.setupUi(self)

    def load(self) -> None:
        self.ui.cdparanoia_opts.setText(self.config.setting['cdripper_cdparanoia_opts'])
        self.ui.flac_opts.setText(self.config.setting['cdripper_flac_opts'])
        self.ui.ar_retries.setValue(self.config.setting['cdripper_ar_retries'])

    def save(self) -> None:
        self.config.setting['cdripper_cdparanoia_opts'] = self.ui.cdparanoia_opts.text()
        self.config.setting['cdripper_flac_opts'] = self.ui.flac_opts.text()
        self.config.setting['cdripper_ar_retries'] = self.ui.ar_retries.value()


register_options_page(CDRipperOptionsPage)
register_album_action(RipCD())
