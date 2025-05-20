from PyQt5.QtWidgets import QSplashScreen
from PyQt5.QtGui import QPixmap, QColor
from PyQt5.QtCore import Qt

class LoadingScreen(QSplashScreen):
    def __init__(self, pixmap):
        super().__init__(pixmap)
        self.setMask(pixmap.mask())
        # self.setAlignment(Qt.AlignCenter) # Remove setAlignment
        self.setStyleSheet("background-color: #f0f2f5;")  # Match main window background

    def showMessage(self, message, alignment=Qt.AlignBottom | Qt.AlignCenter, color=QColor("black")):
        super().showMessage(message, alignment, color)
