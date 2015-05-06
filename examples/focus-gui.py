#!/usr/bin/env python

# python-gphoto2 - Python interface to libgphoto2
# http://github.com/jim-easterbrook/python-gphoto2
# Copyright (C) 2015  Jim Easterbrook  jim@jim-easterbrook.me.uk
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Simple focusing aid - grabs preview images and displays them at full
resolution.

I've written this to work with my Canon EOS 350d, to help me adjust
focus and exposure when using it with a telescope. Many newer cameras
have a 'live preview' mode - this program will need some changes to work
with them.

The focus 'measurement' is simply the rms difference between adjacent
pixels, computed horizontally and vertically. This may well have 'local
maxima' so you need to focus visually first before fine tuning with the
measurement numbers.

The histogram and clipping count are useful when setting exposure.

"""

from __future__ import print_function

from datetime import datetime
import io
import logging
import math
import sys

from PIL import Image, ImageChops, ImageStat
from PyQt4 import QtGui, QtCore
from PyQt4.QtCore import Qt

import gphoto2 as gp

class CameraHandler(QtCore.QObject):
    new_image = QtCore.pyqtSignal(QtGui.QImage)
    new_histogram = QtCore.pyqtSignal(QtGui.QImage)
    new_focus = QtCore.pyqtSignal(list)
    new_clipping = QtCore.pyqtSignal(list)

    def __init__(self):
        self.do_next = QtCore.QEvent.registerEventType()
        super(CameraHandler, self).__init__()
        self.running = False
        # initialise camera
        self.context = gp.Context()
        self.camera = gp.Camera()
        self.camera.init(self.context)
        # get camera config tree
        self.config = self.camera.get_config(self.context)
        # find the capture size class config item
        # need to set this on my Canon 350d to get preview to work at all
        OK, capture_size_class = gp.gp_widget_get_child_by_name(
            self.config, 'capturesizeclass')
        if OK >= gp.GP_OK:
            # set value
            value = capture_size_class.get_choice(2)
            capture_size_class.set_value(value)
            # set config
            self.camera.set_config(self.config, self.context)

    @QtCore.pyqtSlot()
    def one_shot(self):
        if self.running:
            return
        if not self._check_config():
            return
        self._do_capture()

    @QtCore.pyqtSlot()
    def continuous(self):
        if self.running:
            self.running = False
            return
        if not self._check_config():
            return
        self.running = True
        self._do_continuous()

    def shut_down(self):
        self.running = False
        self.camera.exit(self.context)

    def event(self, event):
        if event.type() != self.do_next:
            return super(CameraHandler, self).event(event)
        event.accept()
        self._do_continuous()
        return True

    def _do_continuous(self):
        if not self.running:
            return
        self._do_capture()
        # post event to trigger next capture
        QtGui.QApplication.postEvent(
            self, QtCore.QEvent(self.do_next), Qt.LowEventPriority - 1)

    def _do_capture(self):
        # capture preview image (not saved to camera memory card)
        OK, camera_file = gp.gp_camera_capture_preview(self.camera, self.context)
        if OK < gp.GP_OK:
            print('Failed to capture')
            self.running = False
            return
        file_data = camera_file.get_data_and_size()
        image = Image.open(io.BytesIO(file_data))
        self.image_data = image.tobytes('raw', 'RGB')
        w, h = image.size
        q_image = QtGui.QImage(self.image_data, w, h, QtGui.QImage.Format_RGB888)
        self.new_image.emit(q_image)
        # generate histogram and count clipped pixels
        histogram = image.histogram()
        q_image = QtGui.QImage(100, 256, QtGui.QImage.Format_RGB888)
        q_image.fill(Qt.white)
        clipping = []
        start = 0
        for colour in (0xff0000, 0x00ff00, 0x0000ff):
            stop = start + 256
            band_hist = histogram[start:stop]
            max_value = float(1 + max(band_hist))
            for x in range(len(band_hist)):
                y = float(1 + band_hist[x]) / max_value
                y = 98.0 * max(0.0, 1.0 + (math.log10(y) / 5.0))
                q_image.setPixel(y,     x, colour)
                q_image.setPixel(y + 1, x, colour)
            clipping.append(band_hist[-1])
            start = stop
        self.new_histogram.emit(q_image)
        self.new_clipping.emit(clipping)
        # measure focus by summing inter-pixel differences
        shifted = ImageChops.offset(image, 1, 0)
        diff = ImageChops.difference(image, shifted).crop((1, 0, w, h))
        stats = ImageStat.Stat(diff)
        h_rms = stats.rms
        shifted = ImageChops.offset(image, 0, 1)
        diff = ImageChops.difference(image, shifted).crop((0, 1, w, h))
        stats = ImageStat.Stat(diff)
        rms = stats.rms
        for n in range(len(rms)):
            rms[n] += h_rms[n]
        self.new_focus.emit(rms)

    def _check_config(self):
        # find the image format config item
        OK, image_format = gp.gp_widget_get_child_by_name(
            self.config, 'imageformat')
        if OK >= gp.GP_OK:
            # get current setting
            value = image_format.get_value()
            # make sure it's not raw
            if 'raw' in value.lower():
                print('Cannot preview raw images')
                return False
        return True


class MainWindow(QtGui.QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()
        self.setWindowTitle("Focus assistant")
        # main widget
        widget = QtGui.QWidget()
        widget.setLayout(QtGui.QGridLayout())
        widget.layout().setRowStretch(7, 1)
        self.setCentralWidget(widget)
        # image display area
        self.image_display = QtGui.QLabel()
        scroll_area = QtGui.QScrollArea()
        scroll_area.setWidget(self.image_display)
        scroll_area.setWidgetResizable(True)
        widget.layout().addWidget(scroll_area, 0, 1, 9, 1)
        # histogram
        self.histogram_display = QtGui.QLabel()
        self.histogram_display.setPixmap(QtGui.QPixmap(100, 256))
        self.histogram_display.pixmap().fill(Qt.white)
        widget.layout().addWidget(self.histogram_display, 0, 0)
        # focus measurement
        widget.layout().addWidget(QtGui.QLabel('Focus:'), 1, 0)
        self.focus_display = QtGui.QLabel('-, -, -')
        widget.layout().addWidget(self.focus_display, 2, 0)
        # clipping measurement
        widget.layout().addWidget(QtGui.QLabel('Clipping:'), 3, 0)
        self.clipping_display = QtGui.QLabel('-, -, -')
        widget.layout().addWidget(self.clipping_display, 4, 0)
        # 'single' button
        single_button = QtGui.QPushButton('one-shot')
        single_button.setShortcut('Ctrl+G')
        widget.layout().addWidget(single_button, 5, 0)
        # 'continuous' button
        continuous_button = QtGui.QPushButton('continuous')
        continuous_button.setShortcut('Ctrl+R')
        widget.layout().addWidget(continuous_button, 6, 0)
        # 'quit' button
        quit_button = QtGui.QPushButton('quit')
        quit_button.setShortcut('Ctrl+Q')
        quit_button.clicked.connect(QtGui.qApp.closeAllWindows)
        widget.layout().addWidget(quit_button, 8, 0)
        # create camera handler and run it in a separate thread
        self.ch_thread = QtCore.QThread()
        self.camera_handler = CameraHandler()
        self.camera_handler.moveToThread(self.ch_thread)
        self.ch_thread.start()
        # connect things up
        single_button.clicked.connect(self.camera_handler.one_shot)
        continuous_button.clicked.connect(self.camera_handler.continuous)
        self.camera_handler.new_image.connect(self.new_image)
        self.camera_handler.new_histogram.connect(self.new_histogram)
        self.camera_handler.new_focus.connect(self.new_focus)
        self.camera_handler.new_clipping.connect(self.new_clipping)

    @QtCore.pyqtSlot(QtGui.QImage)
    def new_image(self, image):
        pixmap = QtGui.QPixmap.fromImage(image)
        self.image_display.setPixmap(pixmap)

    @QtCore.pyqtSlot(QtGui.QImage)
    def new_histogram(self, image):
        pixmap = QtGui.QPixmap.fromImage(image)
        self.histogram_display.setPixmap(pixmap)

    @QtCore.pyqtSlot(list)
    def new_focus(self, focus):
        self.focus_display.setText(
            ', '.join(map(lambda x: '{:.2f}'.format(x), focus)))

    @QtCore.pyqtSlot(list)
    def new_clipping(self, clipping):
        self.clipping_display.setText(
            ', '.join(map(lambda x: '{:d}'.format(x), clipping)))

    def closeEvent(self, event):
        self.camera_handler.shut_down()
        self.ch_thread.quit()
        self.ch_thread.wait()
        return super(MainWindow, self).closeEvent(event)


if __name__ == "__main__":
    logging.basicConfig(
        format='%(levelname)s: %(name)s: %(message)s', level=logging.WARNING)
    gp.check_result(gp.use_python_logging())
    app = QtGui.QApplication([])
    main = MainWindow()
    main.show()
    sys.exit(app.exec_())
