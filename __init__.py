# -*- coding: utf-8 -*-

import os
import shutil
import tempfile
import tomllib
from typing import Any, Optional

from PyQt6 import QtCore
from PyQt6.QtWidgets import QDialog

from picard.disc import Disc
from picard.plugin3.api import BaseAction, OptionsPage, PluginApi
from picard.util import sanitize_filename

from . import bridge
from . import ui
from . import ui_options_cdripper

DEFAULT_OPTIONS = {
    'cdparanoia_opts': '--batch 1:-',
    'flac_opts': '--verify --replay-gain --delete-input-file',
    'bridge_enabled': False,
    'bridge_port': 8090,
    'bridge_token': '',
}

_bridge: Optional[bridge.Bridge] = None


class RipCDDialog(QDialog):
    """Dialog for the CD Ripper UI."""
    def __init__(self, parent: Optional[Any]):
        super().__init__(parent)
        self.ui = ui.Ui_CDRipperUI()
        self.ui.setupUi(self)


class RipCD(BaseAction):
    """Album action to start the CD ripper."""
    TITLE = '&Rip CD...'

    def callback(self, objs: list) -> None:
        """Callback to start the CD ripper for the selected album."""
        album = objs[0]
        CDRipper(self.api, album).run()


class CDRipper(QtCore.QObject):
    """Main class for handling CD ripping and encoding."""
    def __init__(self, api: PluginApi, album: Any) -> None:
        super().__init__()
        self.api = api
        self.tagger = api.tagger
        self.log = api.logger
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
        device = self.api.global_config.setting["cd_lookup_device"].split(",", 1)[0]
        disc = Disc()
        disc.read(device)
        self.discid = disc.id
        self.log.debug(f'CD has discid: {disc.id}')
        self.rip()
        result = self.widget.exec()
        if result == QDialog.DialogCode.Rejected:
            self._cleanup()

    def _newProcess(self, output_widget: Any) -> QtCore.QProcess:
        """Create a new QProcess for running external commands."""
        process = QtCore.QProcess()
        process.setWorkingDirectory(self._tmpdir)
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        process.setReadChannel(QtCore.QProcess.ProcessChannel.StandardOutput)
        process.errorOccurred.connect(self.errorHandler)
        # Connect output directly to the correct widget
        process.readyReadStandardOutput.connect(
            lambda p=process, w=output_widget: w.appendPlainText(_decode(p.readAll())))
        # Ensure all output is flushed on finish
        process.finished.connect(
            lambda *_, p=process, w=output_widget: w.appendPlainText(_decode(p.readAll())))
        return process

    def rip(self) -> None:
        """Start the CD ripping process using cdparanoia."""
        process = self._newProcess(self.widget.ui.rip_output)
        self._processes.append(process)
        process.started.connect(self.ripStarted)
        process.finished.connect(self.ripFinished)
        opts = self.api.plugin_config['cdparanoia_opts']
        self.log.debug(f'Using tmp dir: {self._tmpdir}')
        process.start('/usr/bin/cdparanoia', opts.split())

    def ripStarted(self) -> None:
        """Handler for when ripping starts."""
        self.log.debug('Rip started.')
        self._ripping = True
        self.widget.ui.rip_output.appendPlainText('Ripping CD...')
        self.widget.ui.finished_button.setDisabled(True)

    def ripFinished(self, _: Any, status: QtCore.QProcess.ExitStatus) -> None:
        """Handler for when ripping finishes."""
        self._ripping = False
        if status != QtCore.QProcess.ExitStatus.CrashExit:
            self.log.debug('Rip finished; starting encode.')
            self.widget.ui.rip_output.appendPlainText('CD ripping complete!')
            self.encode()

    def encode(self) -> None:
        """Start the encoding process for all tracks using flac."""
        self.widget.ui.encode_output.appendPlainText('Encoding CD...')

        opts = self.api.plugin_config['flac_opts']
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
                f = self.tagger.format_registry.open(path)
                if f is None:
                    self.log.error(f'Could not open encoded file: {path}')
                    continue
                self.tagger.files[path] = f
                f.move(track)
                f.load(lambda *_, **__: True)

            if len(self._flac_files) != self._expected_num_tracks:
                self.log.debug('Ripping/encoding was aborted.')
                return

            self.log.debug('Encoding successful!')
            self.album.load()
            self.widget.ui.finished_button.setEnabled(True)
            self._tmpdir = tempfile.mkdtemp()

    def errorHandler(self, error: QtCore.QProcess.ProcessError) -> None:
        """Handler for process errors."""
        msg = f'Ripping/Encoding failed: {error}'
        self.log.debug(msg)
        self.widget.ui.rip_output.appendPlainText(msg)

    def _cleanup(self) -> None:
        """Clean up all running processes and temporary files."""
        for process in self._processes:
            if process.state() != QtCore.QProcess.ProcessState.NotRunning:
                process.kill()
        try:
            shutil.rmtree(self._tmpdir)
        except OSError as e:
            self.log.debug(f'Failed to remove temp dir: {e}')


def _decode(data: QtCore.QByteArray) -> str:
    """Decode process output, tolerating invalid UTF-8."""
    return bytes(data).decode('utf-8', errors='replace')


class CDRipperOptionsPage(OptionsPage):
    """Options page for the CD Ripper plugin."""
    NAME = 'cdripper'
    TITLE = 'CD Ripper'
    PARENT = 'plugins'

    def __init__(self, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self.ui = ui_options_cdripper.Ui_CDRipperOptionsPage()
        self.ui.setupUi(self)

    def load(self) -> None:
        """Load settings into the options UI."""
        self.ui.cdparanoia_opts.setText(self.api.plugin_config['cdparanoia_opts'])
        self.ui.flac_opts.setText(self.api.plugin_config['flac_opts'])
        self.ui.bridge_enabled.setChecked(bool(self.api.plugin_config['bridge_enabled']))
        self.ui.bridge_port.setValue(int(self.api.plugin_config['bridge_port']))
        self.ui.bridge_token.setText(self.api.plugin_config['bridge_token'])

    def save(self) -> None:
        """Save settings from the options UI."""
        self.api.plugin_config['cdparanoia_opts'] = self.ui.cdparanoia_opts.text()
        self.api.plugin_config['flac_opts'] = self.ui.flac_opts.text()
        self.api.plugin_config['bridge_enabled'] = self.ui.bridge_enabled.isChecked()
        self.api.plugin_config['bridge_port'] = self.ui.bridge_port.value()
        self.api.plugin_config['bridge_token'] = self.ui.bridge_token.text()
        _restart_bridge(self.api)


def _migrate_v2_settings(api: PluginApi) -> None:
    """Copy settings saved by the Picard 2 version of this plugin, if any."""
    setting = api.global_config.setting
    for name in DEFAULT_OPTIONS:
        old_name = f'cdripper_{name}'
        if name not in api.plugin_config and old_name in setting:
            api.plugin_config[name] = setting.raw_value(old_name)


def _plugin_version() -> str:
    try:
        with open(os.path.join(os.path.dirname(__file__), 'MANIFEST.toml'), 'rb') as f:
            return tomllib.load(f).get('version', '')
    except (OSError, tomllib.TOMLDecodeError):
        return ''


def _bridge_settings(api: PluginApi) -> Optional[tuple]:
    """Return (port, token) if the bridge should run, else None.

    CDRIPPER_BRIDGE_PORT and CDRIPPER_BRIDGE_TOKEN_FILE enable it without the
    options page, for containers.
    """
    cfg = api.plugin_config
    port = os.environ.get('CDRIPPER_BRIDGE_PORT')
    token_file = os.environ.get('CDRIPPER_BRIDGE_TOKEN_FILE')
    if port and token_file:
        try:
            with open(token_file, encoding='utf-8') as f:
                return int(port), f.read().strip()
        except (OSError, ValueError) as e:
            api.logger.error(f'Bridge not started: {e}')
            return None
    if cfg['bridge_enabled'] and cfg['bridge_token']:
        return int(cfg['bridge_port']), cfg['bridge_token']
    return None


def _stop_bridge() -> None:
    global _bridge
    if _bridge is not None:
        _bridge.stop()
        _bridge = None


def _restart_bridge(api: PluginApi) -> None:
    """Start, restart or stop the bridge to match the current settings."""
    _stop_bridge()
    settings = _bridge_settings(api)
    if settings is None:
        return
    port, token = settings
    global _bridge
    try:
        _bridge = bridge.Bridge(bridge.Handlers(api, _plugin_version()), token, port,
                                bridge.make_main_thread_caller())
        _bridge.start()
        api.logger.info(f'CD Ripper bridge listening on port {port}')
    except OSError as e:
        api.logger.error(f'Could not start the CD Ripper bridge on port {port}: {e}')


def disable() -> None:
    """Called by Picard when the plugin is disabled."""
    _stop_bridge()


def enable(api: PluginApi) -> None:
    """Called by Picard when the plugin is enabled."""
    for name, default in DEFAULT_OPTIONS.items():
        api.plugin_config.register_option(name, default)
    _migrate_v2_settings(api)
    api.register_options_page(CDRipperOptionsPage)
    api.register_album_action(RipCD)
    _restart_bridge(api)
