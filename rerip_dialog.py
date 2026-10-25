# -*- coding: utf-8 -*-
"""The Verify and re-rip window.

It verifies the album's files against the CD in the drive, lists each
track's result, and re-rips the ticked tracks: each is ripped and encoded on
its own, given its old file's tags, and verified together with the album's
other files. A new rip replaces the old file only when it verifies, or when
the user keeps a re-rip that read the same twice without a skip.
"""

import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Optional

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt

from . import rerip

CDPARANOIA = '/usr/bin/cdparanoia'
FLAC = '/usr/bin/flac'

_COL_RERIP, _COL_NUMBER, _COL_TITLE, _COL_RESULT, _COL_PARANOIA = range(5)


@dataclass
class _Attempt:
    """One re-rip of a track."""
    md5: bytes   # of its audio; empty when unknown
    skips: int   # parts cdparanoia gave up on


class ReRipDialog(QtWidgets.QDialog):
    """Verify an album's files and re-rip the tracks that fail."""

    def __init__(self, parent: Optional[QtWidgets.QWidget], config: Any, title: str,
                 tracks: list, toc: str, preselect: Optional[list] = None,
                 log: Any = None, paranoia: Optional[dict] = None) -> None:
        super().__init__(parent)
        self.config = config  # the plugin's options
        self.tracks = tracks  # rerip.Track, in disc order
        self.toc = toc
        self.preselect = preselect
        self.logger = log
        self.results: dict = {}   # track number -> its latest result
        self.notes: dict = {}     # track number -> what the last re-rip did
        self.paranoia: dict = dict(paranoia or {})  # track number -> cdparanoia's counts
        self.attempts: dict = {}  # track number -> its re-rips, oldest first
        self.consistent: dict = {}  # track number -> a re-rip that may be kept
        self._process: Optional[QtCore.QProcess] = None
        self._workdir = tempfile.mkdtemp(prefix='cdripper-rerip-')
        self._view = os.path.join(self._workdir, 'view')
        self._new = os.path.join(self._workdir, 'new')
        self._keep = os.path.join(self._workdir, 'keep')
        for d in (self._view, self._new, self._keep):
            os.mkdir(d)

        self.setWindowTitle(f'Verify and re-rip: {title}')
        self.resize(900, 520)
        layout = QtWidgets.QVBoxLayout(self)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.table = QtWidgets.QTableWidget(len(tracks), 5)
        self.table.setHorizontalHeaderLabels(['Re-rip', '#', 'Title', 'Result', 'cdparanoia'])
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
            self.table.setItem(row, _COL_PARANOIA, QtWidgets.QTableWidgetItem(''))
        self._show_paranoia()
        layout.addWidget(self.table, 3)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(5000)
        layout.addWidget(self.output, 1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        self.keep_button = QtWidgets.QPushButton('Keep consistent re-rips')
        self.keep_button.setToolTip('Replace the files of tracks whose last two re-rips read the same '
                                    'audio with no skips. They still do not verify.')
        self.keep_button.clicked.connect(self.keep_consistent)
        self.keep_button.setVisible(False)
        self.verify_button = QtWidgets.QPushButton('Verify again')
        self.verify_button.clicked.connect(lambda: self.verify())
        self.rerip_button = QtWidgets.QPushButton('Re-rip selected')
        self.rerip_button.clicked.connect(self.rerip_selected)
        self.close_button = QtWidgets.QPushButton('Close')
        self.close_button.clicked.connect(self.reject)
        for b in (self.keep_button, self.verify_button, self.rerip_button, self.close_button):
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
            now = tracks.get(n)
            if now and now.get('verified'):
                try:
                    rerip.replace_file(new, self._track(n).path)
                except OSError as e:
                    self.notes[n] = f'new rip verified, but replacing the file failed: {e}'
                    continue
                replaced.append(n)
                self.notes[n] = 're-ripped: verified, file replaced'
            elif result.get('status') in ('OK', 'MISS'):
                self.notes[n] = self._unverified(n, new)
            else:
                self.notes[n] = 'new rip could not be verified; original kept'
        if replaced:
            self._log('info', f'CD Ripper: replaced track(s) {replaced} with verified re-rips')
        self.results.update(tracks)
        self._show(result, replaced)

    def _unverified(self, n: int, new: str) -> str:
        """Say what a re-rip that does not verify tells, and offer to keep it
        when it read the same audio as the re-rip before it, without skips."""
        attempts = self.attempts.get(n, [])
        last = attempts[-1] if attempts else _Attempt(b'', 0)
        if last.md5 and last.md5 == self._original_md5(n):
            return ('re-ripped: still does not verify, and read exactly the same audio as the '
                    'current file. The drive reads it the same way every time; original kept')
        if len(attempts) >= 2 and last.md5 and last.md5 == attempts[-2].md5:
            if last.skips or attempts[-2].skips:
                return ('re-ripped: still does not verify; read the same audio as the last re-rip, '
                        'but cdparanoia skipped parts of it, so it may have guessed them. Original kept')
            keep = os.path.join(self._keep, f'{n:02d}.flac')
            try:
                shutil.copyfile(new, keep)
            except OSError as e:
                return f're-ripped: still does not verify; could not hold on to the new rip: {e}'
            self.consistent[n] = keep
            return ('re-ripped: still does not verify, but read the same audio twice with no '
                    'skips. Original kept; Keep consistent re-rips uses this one')
        return 're-ripped: still does not verify; original kept. Re-rip again to see if it reads the same'

    def _original_md5(self, n: int) -> bytes:
        try:
            return rerip.audio_md5(self._track(n).path)
        except Exception:  # mutagen's errors
            return b''

    def keep_consistent(self) -> None:
        """Replace files with re-rips that read the same twice without skips."""
        kept = []
        for n, path in sorted(self.consistent.items()):
            try:
                rerip.replace_file(path, self._track(n).path)
            except OSError as e:
                self.notes[n] = f'could not keep the consistent re-rip: {e}'
                continue
            kept.append(n)
            self.notes[n] = ('kept the consistent re-rip: read the same audio twice with no skips, '
                             'but not verified')
        self.consistent.clear()
        if kept:
            self._log('info', f'CD Ripper: kept consistent, unverified re-rips of track(s) {kept}')
        for row, t in enumerate(self.tracks):
            if t.number in kept:
                self.table.item(row, _COL_RESULT).setText(self.notes[t.number])
        self._tick([])
        names = ', '.join(map(str, kept))
        self._idle(f'Kept the consistent re-rips of track(s) {names}. They read the same twice, '
                   'but no database confirms them.' if kept else 'Nothing was kept.')

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
            self.consistent.pop(n, None)
        self._rip_next(numbers, {})

    def _rip_next(self, numbers: list, done: dict) -> None:
        if not numbers:
            self.verify(done)
            return
        n, rest = numbers[0], numbers[1:]
        self._busy(f'Ripping track {n}…')
        args = rerip.cdparanoia_args(self.config['cdparanoia_opts'], n)
        report = rerip.ParanoiaReport()
        self._run(CDPARANOIA, args, lambda code, _: self._ripped(n, code, report, rest, done),
                  show=report.feed, flush=report.flush)

    def _ripped(self, n: int, code: int, report: rerip.ParanoiaReport, rest: list, done: dict) -> None:
        self.paranoia[n] = report.track()
        self.output.appendPlainText(f'cdparanoia, track {n}: {rerip.paranoia_summary(self.paranoia[n])}')
        self._show_paranoia()
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
            md5 = rerip.audio_md5(out)
            rerip.copy_tags(self._track(n).path, out)
        except Exception as e:  # mutagen raises its own errors
            self._idle(f'Could not copy the tags of track {n} to its new rip: {e}')
            return
        skips = self.paranoia.get(n, {}).get('skip', 0)
        self.attempts.setdefault(n, []).append(_Attempt(md5, skips))
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
        lines += [w for w in result.get('warnings') or [] if w.startswith('CUETools DB: track')]
        if replaced:
            lines.append(f'Replaced track(s) {", ".join(map(str, replaced))} with new rips that verify.')
        lines.append(_advice(result, failing))
        if self.consistent:
            lines.append(f'Track(s) {", ".join(map(str, sorted(self.consistent)))} read the same audio '
                         'twice with no skips. If cleaning the disc and another drive do not help, '
                         'Keep consistent re-rips uses them: the best this disc gives, but unverified.')
        self._idle('\n'.join(line for line in lines if line))

    def _show_paranoia(self) -> None:
        for row, t in enumerate(self.tracks):
            if t.number in self.paranoia:
                text = rerip.paranoia_summary(self.paranoia[t.number])
                self.table.item(row, _COL_PARANOIA).setText(text)

    def _tick(self, numbers: list) -> None:
        for row, t in enumerate(self.tracks):
            state = Qt.CheckState.Checked if t.number in numbers else Qt.CheckState.Unchecked
            self.table.item(row, _COL_RERIP).setCheckState(state)

    def _busy(self, text: str) -> None:
        self.status.setText(text)
        self.output.appendPlainText(text)
        for b in (self.keep_button, self.verify_button, self.rerip_button):
            b.setEnabled(False)

    def _idle(self, text: str) -> None:
        self.status.setText(text)
        for b in (self.keep_button, self.verify_button, self.rerip_button):
            b.setEnabled(True)
        self.keep_button.setVisible(bool(self.consistent))

    # ---- processes --------------------------------------------------------

    def _run(self, program: str, args: list, done: Callable[[int, str], None],
             show: Optional[Callable[[str], str]] = None,
             flush: Optional[Callable[[], str]] = None) -> None:
        """Run program, showing its output, then call done(exit code, output).

        show filters the output before it is shown, and flush returns what
        it held back at the end.
        """
        process = QtCore.QProcess(self)
        process.setWorkingDirectory(self._workdir)
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        out: list = []

        def display(text: str) -> None:
            if text.strip():
                self.output.appendPlainText(text.rstrip('\n'))

        def read() -> None:
            text = bytes(process.readAll()).decode('utf-8', errors='replace')
            if show is not None:
                text = show(text)
            out.append(text)
            display(text)

        def finished(code: int, status: QtCore.QProcess.ExitStatus) -> None:
            read()
            if flush is not None:
                rest = flush()
                out.append(rest)
                display(rest)
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
    if result.get('pressing_offset') and status == 'OK':
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
                'press Re-rip selected, ideally in another drive. A file is replaced only '
                'if its new rip verifies. A drive can read damaged audio the same wrong way '
                'every time, so a clean cdparanoia report does not prove a track is right.')
    return ("No track matches any pressing in the databases. Usually your pressing just "
            "isn't there, so re-ripping tracks won't help.")
