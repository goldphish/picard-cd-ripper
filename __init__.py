#!/usr/bin/env python

PLUGIN_NAME = u'CD Ripper'
PLUGIN_AUTHOR = u'Dan Lange'
PLUGIN_DESCRIPTION = u'Rips CDs and encodes to FLAC.'
PLUGIN_VERSION = '0.1'
PLUGIN_API_VERSIONS = ['0.15']


import os
import subprocess
import traceback
from PyQt4 import QtCore

from picard.config import TextOption
from picard.formats import open as open_file
from picard.plugins.ripper.ui_options_cdripper import Ui_CDRipperOptionsPage
from picard.ui.itemviews import BaseAction, register_album_action
from picard.ui.options import OptionsPage, register_options_page
from picard.util import partial


class RipCD(BaseAction):
  NAME = '&Rip CD...'

  def callback(self, objs):
    album = objs[0]
    library_path = self.config.setting['cdripper_library_path']
    artist_name = album.metadata['albumartist']
    album_name = album.metadata['album']
    self.target_path = os.path.join(library_path, artist_name, album_name)
    try:
      os.makedirs(self.target_path)
    except OSError:
      pass

    self.tagger.other_queue.put((
        partial(self._rip_and_encode, album),
        partial(self._done_callback, album),
        QtCore.Qt.NormalEventPriority))

  def _rip_and_encode(self, album):
    #self._rip(album)
    self._encode(album)

  def _rip(self, album):
    """Rips the CD with cdparanoia to the target_path."""
    self.tagger.window.set_statusbar_message('Ripping CD...')
    opts = self.config.setting['cdripper_cdparanoia_opts']
    cmd_line = 'cdparanoia %s "%s/"' % (opts, self.target_path)
    try:
      # User could shoot themselves in the foot with shell=True here...
      subprocess.check_call(cmd_line, shell=True)
    except subprocess.CalledProcessError:
      album.log.error(traceback.format_exc())

  def _encode(self, album):
    """Encodes the previously ripped CD to Flac."""
    flac_opts = self.config.setting['cdripper_flac_opts']
    try:
      for track in album.tracks:
        self.tagger.window.set_statusbar_message(
            'Encoding "%s"...', track.metadata['title'])
        track_number = track.metadata['tracknumber'].zfill(2)
        wav_name = 'track%s.cdda.wav' % track_number
        wav_path = os.path.join(self.target_path, wav_name)
        flac_name = '%s %s.flac' % (track_number, track.metadata['title'])
        flac_path = os.path.join(self.target_path, flac_name)
        output_args = ('-o', flac_path, wav_path)
        cmd_line = ('flac',) + tuple(flac_opts.split()) + output_args
        #subprocess.check_call(cmd_line)
        f = open_file(flac_path)
        track.add_file(f)
    except subprocess.CalledProcessError:
      album.log.error(traceback.format_exc())
    album.load()

  def _noop_callback(self, result=None, error=None):
    print result
    print dir(result)

  def _done_callback(self, album, result=None, error=None):
    if not error:
      self.tagger.window.set_statusbar_message('Successfully ripped the CD!')
    else:
      self.tagger.window.set_statusbar_message('Error ripping CD.')


class CDRipperOptionsPage(OptionsPage):

  NAME = 'cdripper'
  TITLE = 'CD Ripper'
  PARENT = 'plugins'

  options = [
    TextOption('setting', 'cdripper_library_path', '/tmp'),
    TextOption('setting', 'cdripper_cdparanoia_opts', '--batch 1:-'),
    TextOption(
        'setting', 'cdripper_flac_opts',
        '--verify --replay-gain --delete-input-file'),
  ]

  def __init__(self, parent=None):
    OptionsPage.__init__(self)
    self.ui = Ui_CDRipperOptionsPage()
    self.ui.setupUi(self)

  def load(self):
    self.ui.library_path.setText(self.config.setting['cdripper_library_path'])
    self.ui.cdparanoia_opts.setText(
        self.config.setting['cdripper_cdparanoia_opts'])
    self.ui.flac_opts.setText(self.config.setting['cdripper_flac_opts'])

  def save(self):
    library_path = self.ui.library_path.text()
    cdparanoia_opts = self.ui.cdparanoia_opts.text()
    flac_opts = self.ui.flac_opts.text()
    self.config.setting['cdripper_library_path'] = library_path
    self.config.setting['cdripper_cdparanoia_opts'] = cdparanoia_opts
    self.config.setting['cdripper_flac_opts'] = flac_opts


register_options_page(CDRipperOptionsPage)
action = RipCD()
register_album_action(action)
