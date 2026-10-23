import threading

from . import compatibility


class QtExecutor(object):
    """Run slicer mutations on the Qt thread and return results to network workers."""

    def __init__(self, application, timeout=30):
        self._application = application
        self._timeout = timeout

    def call(self, function, *args):
        if compatibility.is_qt_thread():
            return function(*args)
        event = threading.Event()
        outcome = {"result": None, "error": None}

        def invoke():
            try:
                outcome["result"] = function(*args)
            except Exception as error:
                outcome["error"] = error
            finally:
                event.set()

        self._application.callLater(invoke)
        if not event.wait(self._timeout):
            raise RuntimeError("Timed out waiting for the STE Slicer UI thread")
        if outcome["error"]:
            raise outcome["error"]
        return outcome["result"]
