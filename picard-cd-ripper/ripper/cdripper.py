# -*- coding: utf-8 -*-

import os
import shutil
import subprocess
import tempfile
import threading

from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5.QtWidgets import QDialog

from picard import formats
from picard.config import TextOption
from picard.disc import Disc
from picard.ui.itemviews import BaseAction, register_album_action
from picard.ui.options import OptionsPage, register_options_page
from picard.util import encode_filename, sanitize_filename

ui_options_cdripper = __loader__.load_module('ui_options_cdripper')
ui = __loader__.load_module('ui')


PLUGIN_NAME = u'CD Ripper'
PLUGIN_AUTHOR = u'Dan Lange'
PLUGIN_DESCRIPTION = u'Rips CDs and encodes to FLAC.'
PLUGIN_VERSION = '0.1'
PLUGIN_API_VERSIONS = ['0.15', '2.0']


class RipCDDialog(QDialog):

  def __init__(self, parent):
    QDialog.__init__(self, parent)
    self.ui = ui.Ui_CDRipperUI()
    self.ui.setupUi(self)


class RipCD(BaseAction):
  NAME = '&Rip CD...'

  def callback(self, objs):
    album = objs[0]
    CDRipper(album).run()

class CDRipper(QtCore.QObject):

  def __init__(self, album):
    QtCore.QObject.__init__(self)
    self.album = album
    self.discid = None
    self._ripping = False

    self.widget = RipCDDialog(self.tagger.window)
    self.widget.ui.cancel_button.clicked.connect(self.widget.reject)
    self.widget.ui.finished_button.clicked.connect(self.widget.accept)

    self._tmpdir = tempfile.mkdtemp()
    self._encode_process_count = 0
    self._expected_num_tracks = 0
    self._processes = []  # Prevents garbage collection.
    self._flac_files = []  # Pairs of track objects and path names.

  def run(self):
    device = self.config.setting["cd_lookup_device"].split(",", 1)[0]
    disc = Disc()
    disc.read(encode_filename(device))
    self.discid = disc.id
    self.log.debug('CD has discid: %s', disc.id)
    self.rip()
    result = self.widget.exec_()
    if result == self.widget.Rejected:
      self._cleanup()

  def _newProcess(self):
    process = QtCore.QProcess()
    process.setWorkingDirectory(self._tmpdir)
    process.setProcessChannelMode(process.MergedChannels)
    process.setReadChannel(process.StandardOutput)
    process.error.connect(self.errorHandler)
    process.readyReadStandardOutput.connect(self.readStdOut)
    return process

  def rip(self):
    process = self._newProcess()
    self._processes.append(process)
    process.started.connect(self.ripStarted)
    process.finished.connect(self.ripFinished)
    opts = self.config.setting['cdripper_cdparanoia_opts']
    self.log.debug('Using tmp dir: %s', self._tmpdir)
    process.start('/usr/bin/cdparanoia', opts.split())

  def ripStarted(self):
    self.log.debug('Rip started.')
    self._ripping = True
    self.widget.ui.rip_output.appendPlainText('Ripping CD...')
    self.widget.ui.finished_button.setDisabled(True)

  def ripFinished(self, _, status):
    self._ripping = False
    if status != QtCore.QProcess.CrashExit:
      self.log.debug('Rip finished; starting encode.')
      self.widget.ui.rip_output.appendPlainText('CD ripping complete!')
      self.encode()

  def encode(self):
    self.widget.ui.encode_output.appendPlainText('Encoding CD...')

    opts = self.config.setting['cdripper_flac_opts']
    for track in self.album.tracks:
      if self.discid not in track.metadata['~discids']:
        self.log.debug(
            'discid %s not found in %r',
            self.discid, track.metadata['~discids'])
        continue  # Track is not part of this disc.

      self._expected_num_tracks += 1
      track_title = track.metadata['title']
      track_num = track.metadata['tracknumber'].zfill(2)
      wav_path = os.path.join(self._tmpdir, 'track%s.cdda.wav' % track_num)
      flac_name = sanitize_filename(u'%s %s.flac' % (track_num, track_title))
      flac_path = os.path.join(self._tmpdir, flac_name)
      args = opts.split() + ['-o', flac_path, wav_path]

      process = self._newProcess()
      process.finished.connect(self.encodeFinished)
      process.start('/usr/bin/flac', args)
      self._processes.append(process)
      self._encode_process_count += 1
      self._flac_files.append((flac_path, track))

  def encodeFinished(self):
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

  def errorHandler(self, error):
    msg = 'Ripping/Encoding failed:%s' % error
    self.log.debug(msg)
    self.widget.ui.rip_output.appendPlainText(msg)

  def _cleanup(self):
    for process in self._processes:
      if not process.atEnd():
        process.kill()
    try:
      shutil.rmtree(self._tmpdir)
    except OSError:
      pass

  def readStdOut(self):
    for process in self._processes:
      if self._ripping:
        self.widget.ui.rip_output.appendPlainText(str(process.readAll()))
      else:
        self.widget.ui.encode_output.appendPlainText(str(process.readAll()))


class CDRipperOptionsPage(OptionsPage):

  NAME = 'cdripper'
  TITLE = 'CD Ripper'
  PARENT = 'plugins'

  options = [
    TextOption('setting', 'cdripper_cdparanoia_opts', '--batch 1:-'),
    TextOption(
        'setting', 'cdripper_flac_opts',
        '--verify --replay-gain --delete-input-file'),
  ]

  def __init__(self, parent=None):
    OptionsPage.__init__(self)
    self.ui = ui_options_cdripper.Ui_CDRipperOptionsPage()
    self.ui.setupUi(self)

  def load(self):
    self.ui.cdparanoia_opts.setText(
        self.config.setting['cdripper_cdparanoia_opts'])
    self.ui.flac_opts.setText(self.config.setting['cdripper_flac_opts'])

  def save(self):
    cdparanoia_opts = self.ui.cdparanoia_opts.text()
    self.config.setting['cdripper_cdparanoia_opts'] = cdparanoia_opts
    self.config.setting['cdripper_flac_opts'] = self.ui.flac_opts.text()


register_options_page(CDRipperOptionsPage)
register_album_action(RipCD())
