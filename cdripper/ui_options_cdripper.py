# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'options_cdripper.ui'
#
# Created by: PyQt5 UI code generator 5.5.1
#
# WARNING! All changes made in this file will be lost!

from PyQt5 import QtCore, QtGui, QtWidgets

class Ui_CDRipperOptionsPage(object):
    def setupUi(self, CDRipperOptionsPage):
        CDRipperOptionsPage.setObjectName("CDRipperOptionsPage")
        CDRipperOptionsPage.resize(300, 300)
        self.vboxlayout = QtWidgets.QVBoxLayout(CDRipperOptionsPage)
        self.vboxlayout.setContentsMargins(9, 9, 9, 9)
        self.vboxlayout.setSpacing(6)
        self.vboxlayout.setObjectName("vboxlayout")
        self.media_library_info = QtWidgets.QGroupBox(CDRipperOptionsPage)
        self.media_library_info.setObjectName("media_library_info")
        self.vboxlayout1 = QtWidgets.QVBoxLayout(self.media_library_info)
        self.vboxlayout1.setContentsMargins(9, 9, 9, 9)
        self.vboxlayout1.setSpacing(2)
        self.vboxlayout1.setObjectName("vboxlayout1")
        self.vboxlayout.addWidget(self.media_library_info)
        self.cdparanoia_options = QtWidgets.QGroupBox(CDRipperOptionsPage)
        self.cdparanoia_options.setObjectName("cdparanoia_options")
        self.vboxlayout2 = QtWidgets.QVBoxLayout(self.cdparanoia_options)
        self.vboxlayout2.setContentsMargins(9, 9, 9, 9)
        self.vboxlayout2.setSpacing(2)
        self.vboxlayout2.setObjectName("vboxlayout2")
        self.cdparanoia_opts_label = QtWidgets.QLabel(self.cdparanoia_options)
        self.cdparanoia_opts_label.setObjectName("cdparanoia_opts_label")
        self.vboxlayout2.addWidget(self.cdparanoia_opts_label)
        self.cdparanoia_opts = QtWidgets.QLineEdit(self.cdparanoia_options)
        self.cdparanoia_opts.setObjectName("cdparanoia_opts")
        self.vboxlayout2.addWidget(self.cdparanoia_opts)
        self.vboxlayout.addWidget(self.cdparanoia_options)
        self.flac_options = QtWidgets.QGroupBox(CDRipperOptionsPage)
        self.flac_options.setObjectName("flac_options")
        self.vboxlayout3 = QtWidgets.QVBoxLayout(self.flac_options)
        self.vboxlayout3.setContentsMargins(9, 9, 9, 9)
        self.vboxlayout3.setSpacing(2)
        self.vboxlayout3.setObjectName("vboxlayout3")
        self.flac_opts_label = QtWidgets.QLabel(self.flac_options)
        self.flac_opts_label.setObjectName("flac_opts_label")
        self.vboxlayout3.addWidget(self.flac_opts_label)
        self.flac_opts = QtWidgets.QLineEdit(self.flac_options)
        self.flac_opts.setObjectName("flac_opts")
        self.vboxlayout3.addWidget(self.flac_opts)
        self.vboxlayout.addWidget(self.flac_options)
        self.accuraterip_options = QtWidgets.QGroupBox(CDRipperOptionsPage)
        self.accuraterip_options.setObjectName("accuraterip_options")
        self.vboxlayout4 = QtWidgets.QVBoxLayout(self.accuraterip_options)
        self.vboxlayout4.setContentsMargins(9, 9, 9, 9)
        self.vboxlayout4.setSpacing(2)
        self.vboxlayout4.setObjectName("vboxlayout4")
        self.ar_retries_layout = QtWidgets.QHBoxLayout()
        self.ar_retries_label = QtWidgets.QLabel(self.accuraterip_options)
        self.ar_retries_label.setObjectName("ar_retries_label")
        self.ar_retries_layout.addWidget(self.ar_retries_label)
        self.ar_retries = QtWidgets.QSpinBox(self.accuraterip_options)
        self.ar_retries.setMinimum(0)
        self.ar_retries.setMaximum(10)
        self.ar_retries.setObjectName("ar_retries")
        self.ar_retries_layout.addWidget(self.ar_retries)
        self.ar_retries_layout.addStretch()
        self.vboxlayout4.addLayout(self.ar_retries_layout)
        self.vboxlayout.addWidget(self.accuraterip_options)
        spacerItem = QtWidgets.QSpacerItem(281, 20, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        self.vboxlayout.addItem(spacerItem)

        self.retranslateUi(CDRipperOptionsPage)
        QtCore.QMetaObject.connectSlotsByName(CDRipperOptionsPage)
        CDRipperOptionsPage.setTabOrder(self.cdparanoia_opts, self.flac_opts)

    def retranslateUi(self, CDRipperOptionsPage):
        _translate = QtCore.QCoreApplication.translate
        self.media_library_info.setTitle(_translate("CDRipperOptionsPage", "CD Ripping options"))
        self.cdparanoia_options.setTitle(_translate("CDRipperOptionsPage", "cdparanoia"))
        self.cdparanoia_opts_label.setText(_translate("CDRipperOptionsPage", "Command line options:"))
        self.flac_options.setTitle(_translate("CDRipperOptionsPage", "flac"))
        self.flac_opts_label.setText(_translate("CDRipperOptionsPage", "Command line options:"))
        self.accuraterip_options.setTitle(_translate("CDRipperOptionsPage", "AccurateRip"))
        self.ar_retries_label.setText(_translate("CDRipperOptionsPage", "Max retries per track:"))

