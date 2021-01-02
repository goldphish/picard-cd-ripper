# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'ripper.ui'
#
# Created by: PyQt5 UI code generator 5.5.1
#
# WARNING! All changes made in this file will be lost!

from PyQt5 import QtCore, QtGui, QtWidgets

class Ui_CDRipperUI(object):
    def setupUi(self, CDRipperUI):
        CDRipperUI.setObjectName("CDRipperUI")
        CDRipperUI.resize(1000, 350)
        self.vboxlayout = QtWidgets.QVBoxLayout(CDRipperUI)
        self.vboxlayout.setContentsMargins(9, 9, 9, 9)
        self.vboxlayout.setSpacing(6)
        self.vboxlayout.setObjectName("vboxlayout")
        self.output_label = QtWidgets.QLabel(CDRipperUI)
        self.output_label.setObjectName("output_label")
        self.vboxlayout.addWidget(self.output_label)
        self.ripper_tab = QtWidgets.QTabWidget(CDRipperUI)
        self.ripper_tab.setObjectName("ripper_tab")
        self.cdparanoia = QtWidgets.QWidget()
        self.cdparanoia.setObjectName("cdparanoia")
        self.rip_output = QtWidgets.QPlainTextEdit(self.cdparanoia)
        self.rip_output.setGeometry(QtCore.QRect(0, 0, 981, 241))
        font = QtGui.QFont()
        font.setFamily("Ubuntu Mono")
        self.rip_output.setFont(font)
        self.rip_output.setObjectName("rip_output")
        self.ripper_tab.addTab(self.cdparanoia, "cdparanoia")
        self.flac = QtWidgets.QWidget()
        self.flac.setObjectName("flac")
        self.encode_output = QtWidgets.QPlainTextEdit(self.flac)
        self.encode_output.setGeometry(QtCore.QRect(0, 0, 978, 241))
        self.encode_output.setObjectName("encode_output")
        self.ripper_tab.addTab(self.flac, "flac")
        self.vboxlayout.addWidget(self.ripper_tab)
        self.gridLayout = QtWidgets.QGridLayout()
        self.gridLayout.setObjectName("gridLayout")
        self.cancel_button = QtWidgets.QPushButton(CDRipperUI)
        self.cancel_button.setCheckable(False)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(False)
        self.cancel_button.setObjectName("cancel_button")
        self.gridLayout.addWidget(self.cancel_button, 0, 1, 1, 1)
        self.finished_button = QtWidgets.QPushButton(CDRipperUI)
        self.finished_button.setAutoDefault(False)
        self.finished_button.setDefault(True)
        self.finished_button.setObjectName("finished_button")
        self.gridLayout.addWidget(self.finished_button, 0, 2, 1, 1)
        spacerItem = QtWidgets.QSpacerItem(40, 20, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        self.gridLayout.addItem(spacerItem, 0, 0, 1, 1)
        self.vboxlayout.addLayout(self.gridLayout)

        self.retranslateUi(CDRipperUI)
        self.ripper_tab.setCurrentIndex(0)
        QtCore.QMetaObject.connectSlotsByName(CDRipperUI)

    def retranslateUi(self, CDRipperUI):
        _translate = QtCore.QCoreApplication.translate
        CDRipperUI.setWindowTitle(_translate("CDRipperUI", "CD Ripper"))
        self.output_label.setText(_translate("CDRipperUI", "Output from:"))
        self.cancel_button.setText(_translate("CDRipperUI", "Cancel"))
        self.finished_button.setText(_translate("CDRipperUI", "Finished"))

