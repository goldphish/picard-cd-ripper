# -*- coding: utf-8 -*-

import os
import shutil
import tempfile
import tomllib
from typing import Any, Optional

from PyQt6 import QtCore
from PyQt6.QtWidgets import QDialog, QMessageBox

from picard.disc import Disc
from picard.plugin3.api import BaseAction, OptionsPage, PluginApi
from picard.util import sanitize_filename

from . import bridge
from . import rerip
from . import rerip_dialog
from . import ui
from . import ui_options_cdripper

DEFAULT_OPTIONS = {
    'cdparanoia_opts': '--batch 1:-',
    'flac_opts': '--verify --replay-gain --delete-input-file',
    'bridge_enabled': False,
    'bridge_port': 8090,
    'bridge_token': '',
    'verifier': '/usr/bin/accuraterip-verify',
    'verify_opts': '--offset 6',
    'verify_after_rip': True,
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


class VerifyCD(BaseAction):
    """Album action to verify the rip and re-rip tracks that fail."""
    TITLE = '&Verify and re-rip CD...'

    def callback(self, objs: list) -> None:
        album = objs[0]
        try:
            disc = bridge.read_disc(self.api)
        except bridge.BridgeError as e:
            QMessageBox.warning(self.tagger.window, 'Verify and re-rip', f'Put the CD in the drive: {e}')
            return
        tracks, problem = _album_tracks(album, disc)
        if problem:
            QMessageBox.warning(self.tagger.window, 'Verify and re-rip', problem)
            return
        _open_dialog(self.api, album.metadata['album'], tracks, disc['toc'])


def _album_tracks(album: Any, disc: dict) -> tuple:
    """The album's tracks on the disc in the drive, with their files.

    Returns (tracks, problem): problem says why they can't be verified.
    """
    on_disc = [t for t in album.tracks if disc['discid'] in t.metadata['~musicbrainz_discids']]
    if not on_disc:
        return [], (f'The CD in the drive ({disc["discid"]}) is not attached to this release '
                    'in MusicBrainz. Load the release that matches it.')
    on_disc.sort(key=lambda t: int(t.metadata['tracknumber'] or 0))
    tracks, missing = [], []
    for i, t in enumerate(on_disc, 1):
        files = list(t.iterfiles())
        if len(files) != 1:
            missing.append(str(i))
            continue
        tracks.append(rerip.Track(i, t.metadata['title'], files[0].filename))
    if missing:
        return [], f'Each track needs exactly one file. Tracks without: {", ".join(missing)}.'
    return tracks, ''


# Open Verify and re-rip windows, kept from garbage collection until closed.
_dialogs: list = []


def _open_dialog(api: PluginApi, title: str, tracks: list, toc: str,
                 preselect: Optional[list] = None) -> None:
    """Open a Verify and re-rip window beside Picard's."""
    dialog = rerip_dialog.ReRipDialog(api.tagger.window, api.plugin_config, title, tracks, toc,
                                      preselect, api.logger)
    dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    _dialogs.append(dialog)
    dialog.finished.connect(lambda *_: _dialogs.remove(dialog))
    dialog.show()
    dialog.raise_()
    dialog.start()


def _open_rerip(api: PluginApi, files: list, preselect: list, disc: dict) -> None:
    """Open the Verify and re-rip window for files, at cdcheckd's request."""
    tracks = [rerip.Track(i, rerip.title_of(p), p) for i, p in enumerate(files, 1)]
    title = os.path.basename(os.path.dirname(files[0]))
    _open_dialog(api, title, tracks, disc['toc'], preselect)


class CDRipper(QtCore.QObject):
    """Main class for handling CD ripping and encoding."""
    def __init__(self, api: PluginApi, album: Any) -> None:
        super().__init__()
        self.api = api
        self.tagger = api.tagger
        self.log = api.logger
        self.album = album
        self.discid: Optional[str] = None
        self.toc = ''
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
        self.toc = bridge.disc_info(disc)['toc']
        self.log.debug(f'CD has discid: {disc.id}')
        self.rip()
        result = self.widget.exec()
        if result == QDialog.DialogCode.Rejected:
            self._cleanup()
        elif self._verify_after_rip():
            self.verify()

    def _verify_after_rip(self) -> bool:
        cfg = self.api.plugin_config
        return bool(cfg['verify_after_rip']) and os.access(cfg['verifier'], os.X_OK)

    def verify(self) -> None:
        """Verify the new rip while the CD is still in the drive.

        The window is modal: new files are still in the temporary directory,
        and saving in Picard moves them.
        """
        files = sorted(self._flac_files, key=lambda f: int(f[1].metadata['tracknumber'] or 0))
        tracks = [rerip.Track(i, track.metadata['title'], path)
                  for i, (path, track) in enumerate(files, 1)]
        dialog = rerip_dialog.ReRipDialog(self.tagger.window, self.api.plugin_config,
                                          self.album.metadata['album'], tracks, self.toc,
                                          log=self.log)
        QtCore.QTimer.singleShot(0, dialog.start)
        dialog.exec()

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
            placed = []
            for path, track in self._flac_files:
                path = self._place(path, track)
                if path:
                    placed.append((path, track))
            self._flac_files = placed

            if len(self._flac_files) != self._expected_num_tracks:
                self.log.debug('Ripping/encoding was aborted.')
                return

            self.log.debug('Encoding successful!')
            self.album.load()
            self.widget.ui.finished_button.setEnabled(True)
            self._tmpdir = tempfile.mkdtemp()

    def _place(self, path: str, track: Any) -> Optional[str]:
        """Put a new rip on its track, replacing the track's old file if any.

        A re-rip must not end up beside the old rip: attached as a second
        file on the track, or saved by Picard as "Title (1).flac" because the
        old file holds the name. So when the track already has a file, or a
        file exists where saving would put this one, the new audio replaces
        it in place, keeping its tags. Returns the track's file, or None.
        """
        f = self.tagger.format_registry.open(path)
        if f is None:
            self.log.error(f'Could not open encoded file: {path}')
            return None
        old = self._old_rip(f, path, track)
        if old is None:
            self.tagger.files[path] = f
            f.move(track)
            f.load(lambda *_, **__: True)
            return path
        try:
            rerip.copy_tags(old, path)
            rerip.replace_file(path, old)
        except Exception as e:  # OSError, or mutagen's errors
            self.log.error(f'Could not replace {old} with the new rip: {e}')
            self.widget.ui.encode_output.appendPlainText(f'Could not replace {old}: {e}')
            return None
        os.remove(path)
        self.widget.ui.encode_output.appendPlainText(f'Replaced {old}')
        existing = self.tagger.files.get(old)
        if existing is None:  # saved by an earlier rip, not loaded now
            existing = self.tagger.format_registry.open(old)
            if existing is None:
                return None
            self.tagger.files[old] = existing
            existing.move(track)
            existing.load(lambda *_, **__: True)
        elif existing not in list(track.iterfiles()):
            existing.move(track)
        return old

    def _old_rip(self, f: Any, path: str, track: Any) -> Optional[str]:
        """The file an earlier rip of track left: on the track, or on disk
        where Picard would save this one."""
        for old in track.iterfiles():
            if old.filename != path and os.path.isfile(old.filename):
                return old.filename
        try:
            target = f.make_filename(path, track.metadata)
        except Exception as e:  # a broken naming script
            self.log.debug(f'Could not work out where {path} will be saved: {e}')
            return None
        if target != path and os.path.isfile(target):
            return target
        return None

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
        self.ui.verifier.setText(self.api.plugin_config['verifier'])
        self.ui.verify_opts.setText(self.api.plugin_config['verify_opts'])
        self.ui.verify_after_rip.setChecked(bool(self.api.plugin_config['verify_after_rip']))

    def save(self) -> None:
        """Save settings from the options UI."""
        self.api.plugin_config['cdparanoia_opts'] = self.ui.cdparanoia_opts.text()
        self.api.plugin_config['flac_opts'] = self.ui.flac_opts.text()
        self.api.plugin_config['bridge_enabled'] = self.ui.bridge_enabled.isChecked()
        self.api.plugin_config['bridge_port'] = self.ui.bridge_port.value()
        self.api.plugin_config['bridge_token'] = self.ui.bridge_token.text()
        self.api.plugin_config['verifier'] = self.ui.verifier.text()
        self.api.plugin_config['verify_opts'] = self.ui.verify_opts.text()
        self.api.plugin_config['verify_after_rip'] = self.ui.verify_after_rip.isChecked()
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
        handlers = bridge.Handlers(api, _plugin_version(),
                                   open_rerip=lambda *args: _open_rerip(api, *args))
        _bridge = bridge.Bridge(handlers, token, port,
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
    api.register_album_action(VerifyCD)
    _restart_bridge(api)
