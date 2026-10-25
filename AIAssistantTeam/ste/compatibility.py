"""Imports isolated here so pure core modules remain testable outside STE."""


def is_qt_thread():
    try:
        from PyQt5.QtCore import QCoreApplication, QThread

        application = QCoreApplication.instance()
        return application is not None and QThread.currentThread() == application.thread()
    except Exception:
        return False
