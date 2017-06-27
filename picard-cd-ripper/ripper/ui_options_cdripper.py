# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'options_cdripper.ui'
#
# Created: Tue Oct 30 23:18:24 2012
#      by: PyQt4 UI code generator 4.9.1
#
# WARNING! All changes made in this file will be lost!

from PyQt4 import QtCore, QtGui

try:
  _fromUtf8 = QtCore.QString.fromUtf8
except AttributeError:
  _fromUtf8 = lambda s: s

class Ui_CDRipperOptionsPage(object):
  def setupUi(self, CDRipperOptionsPage):
    CDRipperOptionsPage.setObjectName(_fromUtf8("CDRipperOptionsPage"))
    CDRipperOptionsPage.resize(300, 300)
    self.vboxlayout = QtGui.QVBoxLayout(CDRipperOptionsPage)
    self.vboxlayout.setSpacing(6)
    self.vboxlayout.setMargin(9)
    self.vboxlayout.setObjectName(_fromUtf8("vboxlayout"))
    self.media_library_info = QtGui.QGroupBox(CDRipperOptionsPage)
    self.media_library_info.setObjectName(_fromUtf8("media_library_info"))
    self.vboxlayout1 = QtGui.QVBoxLayout(self.media_library_info)
    self.vboxlayout1.setSpacing(2)
    self.vboxlayout1.setMargin(9)
    self.vboxlayout1.setObjectName(_fromUtf8("vboxlayout1"))
    self.vboxlayout.addWidget(self.media_library_info)
    self.cdparanoia_options = QtGui.QGroupBox(CDRipperOptionsPage)
    self.cdparanoia_options.setObjectName(_fromUtf8("cdparanoia_options"))
    self.vboxlayout2 = QtGui.QVBoxLayout(self.cdparanoia_options)
    self.vboxlayout2.setSpacing(2)
    self.vboxlayout2.setMargin(9)
    self.vboxlayout2.setObjectName(_fromUtf8("vboxlayout2"))
    self.cdparanoia_opts_label = QtGui.QLabel(self.cdparanoia_options)
    self.cdparanoia_opts_label.setObjectName(_fromUtf8("cdparanoia_opts_label"))
    self.vboxlayout2.addWidget(self.cdparanoia_opts_label)
    self.cdparanoia_opts = QtGui.QLineEdit(self.cdparanoia_options)
    self.cdparanoia_opts.setObjectName(_fromUtf8("cdparanoia_opts"))
    self.vboxlayout2.addWidget(self.cdparanoia_opts)
    self.vboxlayout.addWidget(self.cdparanoia_options)
    self.flac_options = QtGui.QGroupBox(CDRipperOptionsPage)
    self.flac_options.setObjectName(_fromUtf8("flac_options"))
    self.vboxlayout3 = QtGui.QVBoxLayout(self.flac_options)
    self.vboxlayout3.setSpacing(2)
    self.vboxlayout3.setMargin(9)
    self.vboxlayout3.setObjectName(_fromUtf8("vboxlayout3"))
    self.flac_opts_label = QtGui.QLabel(self.flac_options)
    self.flac_opts_label.setObjectName(_fromUtf8("flac_opts_label"))
    self.vboxlayout3.addWidget(self.flac_opts_label)
    self.flac_opts = QtGui.QLineEdit(self.flac_options)
    self.flac_opts.setObjectName(_fromUtf8("flac_opts"))
    self.vboxlayout3.addWidget(self.flac_opts)
    self.vboxlayout.addWidget(self.flac_options)
    spacerItem = QtGui.QSpacerItem(281, 20, QtGui.QSizePolicy.Minimum, QtGui.QSizePolicy.Expanding)
    self.vboxlayout.addItem(spacerItem)

    self.retranslateUi(CDRipperOptionsPage)
    QtCore.QMetaObject.connectSlotsByName(CDRipperOptionsPage)
    CDRipperOptionsPage.setTabOrder(self.cdparanoia_opts, self.flac_opts)

  def retranslateUi(self, CDRipperOptionsPage):
    self.media_library_info.setTitle(QtGui.QApplication.translate("CDRipperOptionsPage", "CD Ripping options", None, QtGui.QApplication.UnicodeUTF8))
    self.cdparanoia_options.setTitle(QtGui.QApplication.translate("CDRipperOptionsPage", "cdparanoia", None, QtGui.QApplication.UnicodeUTF8))
    self.cdparanoia_opts_label.setText(QtGui.QApplication.translate("CDRipperOptionsPage", "Command line options:", None, QtGui.QApplication.UnicodeUTF8))
    self.flac_options.setTitle(QtGui.QApplication.translate("CDRipperOptionsPage", "flac", None, QtGui.QApplication.UnicodeUTF8))
    self.flac_opts_label.setText(QtGui.QApplication.translate("CDRipperOptionsPage", "Command line options:", None, QtGui.QApplication.UnicodeUTF8))

