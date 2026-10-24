# -*- coding: utf-8 -*-
"""The Verify and re-rip window.

It verifies the album's files against the CD in the drive, lists each
track's result, and re-rips the ticked tracks: each is ripped and encoded on
its own, given its old file's tags, and verified together with the album's
other files. A new rip replaces the old file only when it verifies.
"""

import os
import shutil
import tempfile
from typing import Any, Callable, Optional

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt

from . import rerip

CDPARANOIA = '/usr/bin/cdparanoia'
FLAC = '/usr/bin/flac'

_COL_RERIP, _COL_NUMBER, _COL_TITLE, _COL_RESULT = range(4)


class ReRipDialog(QtWidgets.QDialog):
    """Verify an album's files and re-rip the tracks that fail."""

    def __init__(self, parent: Optional[QtWidgets.QWidget], config: Any, title: str,
                 tracks: list, toc: str, preselect: Optional[list] = None,
                 log: Any = None) -> None:
        super().__init__(parent)
        self.config = config  # the plugin's options
        self.tracks = tracks  # rerip.Track, in disc order
        self.toc = toc
        self.preselect = preselect
        self.logger = log
        self.results: dict = {}  # track number -> its latest result
        self.notes: dict = {}    # track number -> what the last re-rip did
        self._process: Optional[QtCore.QProcess] = None
        self._workdir = tempfile.mkdtemp(prefix='cdripper-rerip-')
        self._view = os.path.join(self._workdir, 'view')
        self._new = os.path.join(self._workdir, 'new')
        os.mkdir(self._view)
        os.mkdir(self._new)

        self.setWindowTitle(f'Verify and re-rip: {title}')
        self.resize(760, 520)
        layout = QtWidgets.QVBoxLayout(self)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.table = QtWidgets.QTableWidget(len(tracks), 4)
        self.table.setHorizontalHeaderLabels(['Re-rip', '#', 'Title', 'Result'])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(_COL_TITLE, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_RESULT, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for row, t in enumerate(tracks):
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, _COL_RERIP, check)
            self.table.setItem(row, _COL_NUMBER, QtWidgets.QTableWidgetItem(str(t.number)))
            self.table.setItem(row, _COL_TITLE, QtWidgets.QTableWidgetItem(t.title))
            self.table.setItem(row, _COL_RESULT, QtWidgets.QTableWidgetItem(''))
        layout.addWidget(self.table, 3)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(5000)
        layout.addWidget(self.output, 1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        self.verify_button = QtWidgets.QPushButton('Verify again')
        self.verify_button.clicked.connect(lambda: self.verify())
        self.rerip_button = QtWidgets.QPushButton('Re-rip selected')
        self.rerip_button.clicked.connect(self.rerip_selected)
        self.close_button = QtWidgets.QPushButton('Close')
        self.close_button.clicked.connect(self.reject)
        for b in (self.verify_button, self.rerip_button, self.close_button):
            b.setAutoDefault(False)
            buttons.addWidget(b)
        layout.addLayout(buttons)

    # ---- flow -------------------------------------------------------------

    def start(self) -> None:
        """Verify the files, unless the tracks to re-rip are already known."""
        if self.preselect:
            self._tick(self.preselect)
            names = ', '.join(map(str, self.preselect))
            self._idle(f'Tracks that did not verify: {names}. With the CD in the drive, '
                       'press Re-rip selected. A file is replaced only if its new rip verifies.')
        else:
            self.verify()

    def verify(self, replacements: Optional[dict] = None) -> None:
        """Verify the album, with replacements (new rips) standing in for files."""
        replacements = replacements or {}
        try:
            rerip.make_view(self.tracks, replacements, self._view)
        except OSError as e:
            self._idle(f'Could not prepare the files for verification: {e}')
            return
        self._busy('Verifying against AccurateRip and the CUETools DB…')
        args = rerip.verifier_args(self.config['verify_opts'], self.toc, self._view)
        self._run(self.config['verifier'], args,
                  lambda _, out: self._verified(rerip.parse_result(out), replacements))

    def _verified(self, result: dict, replacements: dict) -> None:
        tracks = rerip.track_results(result)
        replaced = []
        for n, new in sorted(replacements.items()):
            now, before = tracks.get(n), self.results.get(n)
            if now and now.get('verified'):
                try:
                    rerip.replace_file(new, self._track(n).path)
                except OSError as e:
                    self.notes[n] = f'new rip verified, but replacing the file failed: {e}'
                    continue
                replaced.append(n)
                self.notes[n] = 're-ripped: verified, file replaced'
            elif result.get('status') in ('OK', 'MISS'):
                same = bool(now and before) and now.get('crc_v1') == before.get('crc_v1')
                self.notes[n] = ('re-ripped: still does not verify'
                                 + (', with the same CRC: the drive reads it the same way every time'
                                    if same else '')
                                 + '; original kept')
            else:
                self.notes[n] = 'new rip could not be verified; original kept'
        if replaced:
            self._log('info', f'CD Ripper: replaced track(s) {replaced} with verified re-rips')
        self.results.update(tracks)
        self._show(result, replaced)

    def rerip_selected(self) -> None:
        numbers = [t.number for row, t in enumerate(self.tracks)
                   if self.table.item(row, _COL_RERIP).checkState() == Qt.CheckState.Checked]
        if not numbers:
            self._idle('Tick the tracks to re-rip first.')
            return
        for name in os.listdir(self._new):
            os.remove(os.path.join(self._new, name))
        for n in numbers:
            self.notes.pop(n, None)
        self._rip_next(numbers, {})

    def _rip_next(self, numbers: list, done: dict) -> None:
        if not numbers:
            self.verify(done)
            return
        n, rest = numbers[0], numbers[1:]
        self._busy(f'Ripping track {n}…')
        args = rerip.cdparanoia_args(self.config['cdparanoia_opts'], n)
        self._run(CDPARANOIA, args, lambda code, _: self._ripped(n, code, rest, done))

    def _ripped(self, n: int, code: int, rest: list, done: dict) -> None:
        wav = os.path.join(self._workdir, rerip.wav_name(n))
        if code != 0 or not os.path.exists(wav):
            self._idle(f'cdparanoia could not rip track {n} (exit code {code}). '
                       'Is the CD in the drive?')
            return
        out = os.path.join(self._new, f'{n:02d}.flac')
        self._busy(f'Encoding track {n}…')
        args = rerip.flac_args(self.config['flac_opts'], wav, out)
        self._run(FLAC, args, lambda code, _: self._encoded(n, code, wav, out, rest, done))

    def _encoded(self, n: int, code: int, wav: str, out: str, rest: list, done: dict) -> None:
        if os.path.exists(wav):
            os.remove(wav)
        if code != 0 or not os.path.exists(out):
            self._idle(f'flac could not encode track {n} (exit code {code}).')
            return
        try:
            rerip.copy_tags(self._track(n).path, out)
        except Exception as e:  # mutagen raises its own errors
            self._idle(f'Could not copy the tags of track {n} to its new rip: {e}')
            return
        done[n] = out
        self._rip_next(rest, done)

    # ---- display ----------------------------------------------------------

    def _show(self, result: dict, replaced: list) -> None:
        tracks = rerip.track_results(result)
        failing = rerip.failing(result)
        for row, t in enumerate(self.tracks):
            text = rerip.describe(tracks[t.number]) if t.number in tracks else ''
            if t.number in self.notes:
                text = f'{self.notes[t.number]} ({text})' if text else self.notes[t.number]
            self.table.item(row, _COL_RESULT).setText(text)
        self._tick(failing)
        lines = [rerip.summary(result)]
        if replaced:
            lines.append(f'Replaced track(s) {", ".join(map(str, replaced))} with new rips that verify.')
        lines.append(_advice(result, failing))
        self._idle('\n'.join(line for line in lines if line))

    def _tick(self, numbers: list) -> None:
        for row, t in enumerate(self.tracks):
            state = Qt.CheckState.Checked if t.number in numbers else Qt.CheckState.Unchecked
            self.table.item(row, _COL_RERIP).setCheckState(state)

    def _busy(self, text: str) -> None:
        self.status.setText(text)
        self.output.appendPlainText(text)
        for b in (self.verify_button, self.rerip_button):
            b.setEnabled(False)

    def _idle(self, text: str) -> None:
        self.status.setText(text)
        for b in (self.verify_button, self.rerip_button):
            b.setEnabled(True)

    # ---- processes --------------------------------------------------------

    def _run(self, program: str, args: list, done: Callable[[int, str], None]) -> None:
        """Run program, showing its output, then call done(exit code, output)."""
        process = QtCore.QProcess(self)
        process.setWorkingDirectory(self._workdir)
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        out: list = []

        def read() -> None:
            text = bytes(process.readAll()).decode('utf-8', errors='replace')
            out.append(text)
            self.output.appendPlainText(text.rstrip('\n'))

        def finished(code: int, status: QtCore.QProcess.ExitStatus) -> None:
            read()
            self._process = None
            if status == QtCore.QProcess.ExitStatus.CrashExit:
                self._idle(f'{os.path.basename(program)} stopped unexpectedly.')
                return
            done(code, ''.join(out))

        def failed(error: QtCore.QProcess.ProcessError) -> None:
            if error == QtCore.QProcess.ProcessError.FailedToStart:
                self._process = None
                self._idle(f'Could not run {program}. Is it installed? '
                           '(Options → Plugins → CD Ripper)')

        process.readyReadStandardOutput.connect(read)
        process.finished.connect(finished)
        process.errorOccurred.connect(failed)
        self._process = process
        process.start(program, args)

    def done(self, r: int) -> None:
        """Stop any running process and remove the work directory on close."""
        if self._process is not None:
            self._process.finished.disconnect()
            self._process.kill()
            self._process.waitForFinished(3000)
            self._process = None
        shutil.rmtree(self._workdir, ignore_errors=True)
        super().done(r)

    def _track(self, n: int) -> rerip.Track:
        return next(t for t in self.tracks if t.number == n)

    def _log(self, level: str, msg: str) -> None:
        if self.logger is not None:
            getattr(self.logger, level)(msg)


def _advice(result: dict, failing: list) -> str:
    """What a result means for the rip, as cdcheck's phone app puts it."""
    status = result.get('status')
    if result.get('pressing_offset'):
        return ('The audio matches another pressing of this CD, so the rip is accurate. '
                'If most CDs show the same, the drive offset setting is wrong instead.')
    if status == 'OK':
        return 'Every track verified.'
    if status == 'NONE':
        return "This pressing isn't in either database, so the rip can't be verified."
    if status != 'MISS':
        return ''
    if failing:
        return ('Ticked tracks did not verify while the others did, so this is the right '
                'disc and offset: they most likely have read errors. Clean the disc and '
                'press Re-rip selected. A file is replaced only if its new rip verifies.')
    return ("No track matches any pressing in the databases. Usually your pressing just "
            "isn't there, so re-ripping tracks won't help.")
