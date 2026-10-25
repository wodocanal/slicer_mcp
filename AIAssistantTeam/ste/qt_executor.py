import threading

from . import compatibility


class QtExecutor(object):
    """Run slicer mutations on the Qt thread and return results to network workers."""

    def __init__(self, application, timeout=120):
        self._application = application
        self._timeout = timeout

    def submit(self, function, *args):
        """Queue work on Qt without blocking the MCP network worker."""
        if compatibility.is_qt_thread():
            return function(*args)

        def invoke():
            try:
                self._process_events()
                function(*args)
            except Exception:
                # submit() is used only for best-effort snapshot refreshes.
                # A disappearing scene during shutdown must not escape into Qt.
                pass
            finally:
                self._process_events()

        self._application.callLater(invoke)
        return None

    def call(self, function, *args):
        if compatibility.is_qt_thread():
            return function(*args)
        event = threading.Event()
        outcome = {"result": None, "error": None}

        def invoke():
            try:
                self._process_events()
                outcome["result"] = function(*args)
            except Exception as error:
                outcome["error"] = error
            finally:
                self._process_events()
                event.set()

        self._application.callLater(invoke)
        if not event.wait(self._timeout):
            raise RuntimeError("Timed out waiting for the STE Slicer UI thread")
        if outcome["error"]:
            raise outcome["error"]
        return outcome["result"]

    @staticmethod
    def _process_events():
        """Drain already queued Qt work only while executing on the UI thread."""
        if not compatibility.is_qt_thread():
            return
        try:
            from PyQt5.QtCore import QCoreApplication

            application = QCoreApplication.instance()
            if application is not None:
                application.processEvents()
        except Exception:
            # Event pumping is a responsiveness aid; it must never mask the
            # actual tool result or make the executor unusable during shutdown.
            pass
