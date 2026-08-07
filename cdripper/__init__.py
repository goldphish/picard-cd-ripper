# -*- coding: utf-8 -*-

import os
import shutil
import tempfile
from typing import Any, Optional

from PyQt5 import QtCore
from PyQt5.QtWidgets import QDialog
from PyQt5.QtCore import pyqtSignal, QObject

from picard import formats
from picard.config import TextOption
from picard.disc import Disc
from picard.plugins.cdripper import ui
from picard.plugins.cdripper import ui_options_cdripper
from picard.ui.itemviews import BaseAction, register_album_action
from picard.ui.options import OptionsPage, register_options_page
from picard.util import encode_filename, sanitize_filename

PLUGIN_NAME = 'CD Ripper'
PLUGIN_AUTHOR = 'Dan Lange'
PLUGIN_DESCRIPTION = 'Rips CDs and encodes to FLAC.'
PLUGIN_VERSION = '0.3.2'
PLUGIN_API_VERSIONS = ['0.15', '2.0', '2.7', '3.0']
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
        """Callback to start the CD ripper for the selected album."""
        album = objs[0]
        CDRipper(album).run()

class CDRipper(QtCore.QObject):
    """Main class for handling CD ripping and encoding."""
    def __init__(self, album: Any) -> None:
        super().__init__()
        self.album = album
        self.discid: Optional[str] = None
        self._ripping: bool = False

        self.widget = RipCDDialog(self.tagger.window)
        self.widget.ui.cancel_button.clicked.connect(self.widget.reject)
        self.widget.ui.finished_button.clicked.connect(self.widget.accept)

        self._tmpdir = tempfile.mkdtemp()
        self._encode_process_count = 0
        self._expected_num_tracks = 0
        self._processes = []  # Prevents garbage collection.
        self._flac_files = []  # Pairs of track objects and path names.

    def run(self) -> None:
        """Run the CD ripper dialog and start the ripping process."""
        device = self.config.setting["cd_lookup_device"].split(",", 1)[0]
        disc = Disc()
        disc.read(encode_filename(device))
        self.discid = disc.id
        self.log.debug(f'CD has discid: {disc.id}')
        self.rip()
        result = self.widget.exec_()
        if result == self.widget.Rejected:
            self._cleanup()

    def _newProcess(self, output_widget: Any) -> QtCore.QProcess:
        """Create a new QProcess for running external commands."""
        process = QtCore.QProcess()
        process.setWorkingDirectory(self._tmpdir)
        process.setProcessChannelMode(process.MergedChannels)
        process.setReadChannel(process.StandardOutput)
        process.error.connect(self.errorHandler)
        # Connect output directly to the correct widget
        process.readyReadStandardOutput.connect(
            lambda p=process, w=output_widget: w.appendPlainText(str(p.readAll(), 'utf-8')))
        # Ensure all output is flushed on finish
        process.finished.connect(
            lambda *_, p=process, w=output_widget: w.appendPlainText(str(p.readAll(), 'utf-8')))
        return process

    def rip(self) -> None:
        """Start the CD ripping process using cdparanoia."""
        process = self._newProcess(self.widget.ui.rip_output)
        self._processes.append(process)
        process.started.connect(self.ripStarted)
        process.finished.connect(self.ripFinished)
        opts = self.config.setting['cdripper_cdparanoia_opts']
        self.log.debug(f'Using tmp dir: {self._tmpdir}')
        process.start('/usr/bin/cdparanoia', opts.split())

    def ripStarted(self) -> None:
        """Handler for when ripping starts."""
        self.log.debug('Rip started.')
        self._ripping = True
        self.widget.ui.rip_output.appendPlainText('Ripping CD...')
        self.widget.ui.finished_button.setDisabled(True)

    def ripFinished(self, _: Any, status: int) -> None:
        """Handler for when ripping finishes."""
        self._ripping = False
        if status != QtCore.QProcess.CrashExit:
            self.log.debug('Rip finished; starting encode.')
            self.widget.ui.rip_output.appendPlainText('CD ripping complete!')
            self.encode()

    def encode(self) -> None:
        """Start the encoding process for all tracks using flac."""
        self.widget.ui.encode_output.appendPlainText('Encoding CD...')

        opts = self.config.setting['cdripper_flac_opts']
        for track in self.album.tracks:
            if self.discid not in track.metadata['~musicbrainz_discids']:
                self.log.error(
                    f'discid {self.discid} not found in {track.metadata["~musicbrainz_discids"]}')
                continue  # Track is not part of this disc.

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
        """Handler for when encoding finishes for a track."""
        self._encode_process_count -= 1
        if self._encode_process_count == 0:
            for path, track in self._flac_files:
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

    def errorHandler(self, error: Any) -> None:
        """Handler for process errors."""
        msg = f'Ripping/Encoding failed: {error}'
        self.log.debug(msg)
        self.widget.ui.rip_output.appendPlainText(msg)

    def _cleanup(self) -> None:
        """Clean up all running processes and temporary files."""
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
        TextOption(
            'setting', 'cdripper_flac_opts',
            '--verify --replay-gain --delete-input-file'),
    ]

    def __init__(self, parent: Optional[Any] = None) -> None:
        super().__init__()
        self.ui = ui_options_cdripper.Ui_CDRipperOptionsPage()
        self.ui.setupUi(self)

    def load(self) -> None:
        """Load settings into the options UI."""
        self.ui.cdparanoia_opts.setText(
            self.config.setting['cdripper_cdparanoia_opts'])
        self.ui.flac_opts.setText(self.config.setting['cdripper_flac_opts'])

    def save(self) -> None:
        """Save settings from the options UI."""
        cdparanoia_opts = self.ui.cdparanoia_opts.text()
        self.config.setting['cdripper_cdparanoia_opts'] = cdparanoia_opts
        self.config.setting['cdripper_flac_opts'] = self.ui.flac_opts.text()

register_options_page(CDRipperOptionsPage)
register_album_action(RipCD())
