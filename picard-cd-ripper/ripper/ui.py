# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'ripper.ui'
#
# Created: Mon Oct 29 23:27:16 2012
#      by: PyQt4 UI code generator 4.9.1
#
# WARNING! All changes made in this file will be lost!

from PyQt4 import QtCore, QtGui

try:
  _fromUtf8 = QtCore.QString.fromUtf8
except AttributeError:
  _fromUtf8 = lambda s: s

class Ui_CDRipperUI(object):
  def setupUi(self, CDRipperUI):
    CDRipperUI.setObjectName(_fromUtf8("CDRipperUI"))
    CDRipperUI.resize(1000, 350)
    self.vboxlayout = QtGui.QVBoxLayout(CDRipperUI)
    self.vboxlayout.setSpacing(6)
    self.vboxlayout.setMargin(9)
    self.vboxlayout.setObjectName(_fromUtf8("vboxlayout"))
    self.output_label = QtGui.QLabel(CDRipperUI)
    self.output_label.setObjectName(_fromUtf8("output_label"))
    self.vboxlayout.addWidget(self.output_label)
    self.ripper_tab = QtGui.QTabWidget(CDRipperUI)
    self.ripper_tab.setObjectName(_fromUtf8("ripper_tab"))
    self.cdparanoia = QtGui.QWidget()
    self.cdparanoia.setObjectName(_fromUtf8("cdparanoia"))
    self.rip_output = QtGui.QPlainTextEdit(self.cdparanoia)
    self.rip_output.setGeometry(QtCore.QRect(0, 0, 981, 241))
    font = QtGui.QFont()
    font.setFamily(_fromUtf8("Ubuntu Mono"))
    self.rip_output.setFont(font)
    self.rip_output.setObjectName(_fromUtf8("rip_output"))
    self.ripper_tab.addTab(self.cdparanoia, _fromUtf8("cdparanoia"))
    self.flac = QtGui.QWidget()
    self.flac.setObjectName(_fromUtf8("flac"))
    self.encode_output = QtGui.QPlainTextEdit(self.flac)
    self.encode_output.setGeometry(QtCore.QRect(0, 0, 978, 241))
    self.encode_output.setObjectName(_fromUtf8("encode_output"))
    self.ripper_tab.addTab(self.flac, _fromUtf8("flac"))
    self.vboxlayout.addWidget(self.ripper_tab)
    self.gridLayout = QtGui.QGridLayout()
    self.gridLayout.setObjectName(_fromUtf8("gridLayout"))
    self.cancel_button = QtGui.QPushButton(CDRipperUI)
    self.cancel_button.setCheckable(False)
    self.cancel_button.setAutoDefault(False)
    self.cancel_button.setDefault(False)
    self.cancel_button.setObjectName(_fromUtf8("cancel_button"))
    self.gridLayout.addWidget(self.cancel_button, 0, 1, 1, 1)
    self.finished_button = QtGui.QPushButton(CDRipperUI)
    self.finished_button.setAutoDefault(False)
    self.finished_button.setDefault(True)
    self.finished_button.setObjectName(_fromUtf8("finished_button"))
    self.gridLayout.addWidget(self.finished_button, 0, 2, 1, 1)
    spacerItem = QtGui.QSpacerItem(40, 20, QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Minimum)
    self.gridLayout.addItem(spacerItem, 0, 0, 1, 1)
    self.vboxlayout.addLayout(self.gridLayout)

    self.retranslateUi(CDRipperUI)
    self.ripper_tab.setCurrentIndex(0)
    QtCore.QMetaObject.connectSlotsByName(CDRipperUI)

  def retranslateUi(self, CDRipperUI):
    CDRipperUI.setWindowTitle(QtGui.QApplication.translate("CDRipperUI", "CD Ripper", None, QtGui.QApplication.UnicodeUTF8))
    self.output_label.setText(QtGui.QApplication.translate("CDRipperUI", "Output from:", None, QtGui.QApplication.UnicodeUTF8))
    self.cancel_button.setText(QtGui.QApplication.translate("CDRipperUI", "Cancel", None, QtGui.QApplication.UnicodeUTF8))
    self.finished_button.setText(QtGui.QApplication.translate("CDRipperUI", "Finished", None, QtGui.QApplication.UnicodeUTF8))

