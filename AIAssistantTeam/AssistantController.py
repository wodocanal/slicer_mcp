import base64
import ctypes
import ctypes.wintypes
import json
import os
import socket
import subprocess
import sys
import threading
import time

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import ProxyHandler, Request, build_opener
except ImportError:  # pragma: no cover - Python 2 compatibility in old Cura forks
    from urllib2 import HTTPError, ProxyHandler, Request, URLError, build_opener

from PyQt5.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Logger import Logger

from .core.llm import Agent, ChatClient
from .core.optimization import map_optimization_status


DEFAULT_BASE_URL = "https://api.timeweb.ai/v1"
DEFAULT_MODEL = "dashscope/qwen3.8-flash"


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.wintypes.DWORD),
        ("SchedulingClass", ctypes.wintypes.DWORD),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _assign_kill_on_close_job(process):
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = ctypes.wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    information = _JobExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = 0x00002000
    configured = kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(information), ctypes.sizeof(information)
    )
    assigned = configured and kernel32.AssignProcessToJobObject(
        job, ctypes.wintypes.HANDLE(process._handle)
    )
    if not assigned:
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise ctypes.WinError(error)
    return job


def _close_job(job):
    if job and os.name == "nt":
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(job)


class AssistantController(QObject):
    messagesChanged = pyqtSignal()
    busyChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    attachmentChanged = pyqtSignal()
    economicsChanged = pyqtSignal()
    optimizationChanged = pyqtSignal()

    def __init__(self, application, registry, mcp_server, parent=None):
        QObject.__init__(self, parent)
        self._app = application
        self._registry = registry
        self._mcp_server = mcp_server
        self._messages = []
        self._history = []
        self._attachments = []
        self._attachment_error = ""
        self._busy = False
        self._economics = {"slicing": None, "quote": None, "baseline": None,
                           "candidate": None, "comparison": None, "error": ""}
        self._optimization = {"checking": False, "capabilities": None,
                              "templates": [], "error": ""}
        self._bridge_start_error = ""
        preferences = application.getPreferences()
        preferences.addPreference("ai_assistant/base_url", DEFAULT_BASE_URL)
        preferences.addPreference("ai_assistant/api_key", "")
        preferences.addPreference("ai_assistant/model", DEFAULT_MODEL)
        preferences.addPreference("ai_assistant/history", "[]")
        preferences.addPreference(
            "ai_assistant/optimization_url", "http://127.0.0.1:8765"
        )
        preferences.addPreference("ai_assistant/optimization_token", "")
        self._preferences = preferences
        self._load_history()

    @pyqtProperty(str, notify=messagesChanged)
    def messagesJson(self):
        return json.dumps(self._messages, ensure_ascii=False)

    @pyqtProperty(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @pyqtProperty(str, notify=settingsChanged)
    def baseUrl(self):
        return self._preferences.getValue("ai_assistant/base_url") or ""

    @pyqtProperty(str, notify=settingsChanged)
    def apiKey(self):
        return self._preferences.getValue("ai_assistant/api_key") or ""

    @pyqtProperty(str, notify=settingsChanged)
    def model(self):
        return self._preferences.getValue("ai_assistant/model") or ""

    @pyqtProperty(str, notify=attachmentChanged)
    def attachmentLabel(self):
        if not self._attachments:
            return self._attachment_error or "Файлы не выбраны"
        return ", ".join(os.path.basename(path) for path in self._attachments)

    @pyqtProperty(str, notify=economicsChanged)
    def economicsJson(self):
        return json.dumps(self._economics, ensure_ascii=False)

    @pyqtProperty(str, notify=optimizationChanged)
    def optimizationJson(self):
        return json.dumps(self._optimization, ensure_ascii=False)

    @pyqtProperty(str, notify=optimizationChanged)
    def optimizationUrl(self):
        return self._preferences.getValue("ai_assistant/optimization_url") or ""

    @pyqtProperty(str, notify=optimizationChanged)
    def optimizationToken(self):
        return self._preferences.getValue("ai_assistant/optimization_token") or ""

    _bridge_proc = None
    _bridge_job = None

    @pyqtProperty(bool, notify=optimizationChanged)
    def bridgeRunning(self):
        process = AssistantController._bridge_proc
        return bool(process is not None and process.poll() is None)

    def _find_bridge(self):
        plugin_root = os.path.dirname(os.path.abspath(__file__))
        server = os.path.join(plugin_root, "optimization_bridge", "server.py")
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        current_executable = os.path.abspath(sys.executable or "")
        current_name = os.path.basename(current_executable).lower()
        candidates = []
        if current_name in ("python.exe", "pythonw.exe", "python3.exe", "python3w.exe"):
            candidates.append(current_executable)
        candidates.extend([
            os.path.join(local_app_data, "SlicerMCP", "TopOptBridge", ".venv",
                         "Scripts", "python.exe"),
            os.path.join(local_app_data, "SlicerMCP", "TopOptBridge", ".venv",
                         "Scripts", "pythonw.exe"),
            os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
            os.path.join(local_app_data, "Programs", "Python", "Python312", "pythonw.exe"),
        ])
        python_executable = next(
            (path for path in candidates if os.path.isfile(path)), None
        )
        if not os.path.isfile(server) or not python_executable:
            return None, None
        return python_executable, server

    def _bridge_runtime_paths(self):
        runtime_root = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "Stereotech",
            "TopOpt",
        )
        return {
            "root": runtime_root,
            "connection": os.path.join(runtime_root, "connection.json"),
            "log": os.path.join(runtime_root, "bridge.log"),
        }

    def _read_bridge_connection(self):
        try:
            with open(self._bridge_runtime_paths()["connection"], "r") as stream:
                value = json.load(stream)
            if not isinstance(value, dict):
                return {}
            if value.get("url") != "http://127.0.0.1:8765":
                return {}
            if int(value.get("port", -1)) != 8765:
                return {}
            if len(str(value.get("token") or "")) < 32:
                return {}
            if int(value.get("pid", -1)) <= 0:
                return {}
            return value
        except (IOError, OSError, ValueError, TypeError):
            pass
        return {}

    def _remove_bridge_connection(self):
        try:
            os.remove(self._bridge_runtime_paths()["connection"])
        except OSError:
            pass

    def _bridge_port_listening(self, timeout=0.4):
        connection = None
        try:
            connection = socket.create_connection(("127.0.0.1", 8765), timeout)
            return True
        except socket.error:
            return False
        finally:
            if connection is not None:
                connection.close()

    def _bridge_health_ok(self, connection, timeout=1.5):
        token = str(connection.get("token") or "")
        if not token:
            return False
        request = Request(
            "http://127.0.0.1:8765/health",
            headers={"Accept": "application/json", "X-Bridge-Token": token},
        )
        try:
            response = build_opener(ProxyHandler({})).open(request, timeout=timeout)
            return int(response.getcode()) == 200
        except (HTTPError, URLError, ValueError, socket.error):
            return False

    def _terminate_bridge_pid(self, pid):
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        if pid <= 0 or pid == os.getpid():
            return False
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        result = subprocess.call(
            ["taskkill", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return result == 0

    def _preflight_bridge(self):
        connection = self._read_bridge_connection()
        if not self._bridge_port_listening():
            self._remove_bridge_connection()
            return False
        if self._bridge_health_ok(connection):
            return True
        if not self._terminate_bridge_pid(connection.get("pid")):
            raise RuntimeError(
                "Port 8765 is occupied, but the stale TopOpt process cannot be identified"
            )
        deadline = time.time() + 3.0
        while time.time() < deadline and self._bridge_port_listening(0.15):
            time.sleep(0.1)
        if self._bridge_port_listening(0.15):
            raise RuntimeError("The stale TopOpt process did not release port 8765")
        self._remove_bridge_connection()
        return False

    def _wait_for_bridge_start(self, process, token, timeout=4.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if process.poll() is not None:
                Logger.log("w", "TopOpt bridge exited during startup with code %s",
                           str(process.returncode))
                return False
            try:
                connection = self._read_bridge_connection()
                if (str(connection.get("token") or "") == token
                        and self._bridge_health_ok(connection, 0.5)):
                    return True
            except (ValueError, TypeError):
                pass
            time.sleep(0.2)
        Logger.log("w", "TopOpt bridge did not publish its connection in time")
        return False

    def _ensure_bridge_started(self):
        self._bridge_start_error = ""
        python_executable, server = self._find_bridge()
        if not python_executable:
            self._bridge_start_error = (
                "TopOpt bridge cannot start: a Python executable or server.py "
                "was not found"
            )
            return False
        try:
            paths = self._bridge_runtime_paths()
            if not os.path.isdir(paths["root"]):
                os.makedirs(paths["root"])
            if self._preflight_bridge():
                return True
            flags = 0x08000000 if os.name == "nt" else 0
            token = str(
                self._preferences.getValue("ai_assistant/optimization_token") or ""
            ).strip()
            if len(token) < 32:
                token = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
            command = [
                python_executable,
                server,
                "--token", token,
                "--port", "8765",
            ]
            with open(paths["log"], "ab") as bridge_log:
                AssistantController._bridge_proc = subprocess.Popen(
                    command,
                    stdout=bridge_log,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", flags),
                )
            AssistantController._bridge_job = _assign_kill_on_close_job(
                AssistantController._bridge_proc
            )
            if self._wait_for_bridge_start(AssistantController._bridge_proc, token):
                return True
            self._bridge_start_error = (
                "TopOpt bridge did not create connection.json and answer /health "
                "within 4 seconds; see bridge.log"
            )
            process = AssistantController._bridge_proc
            if process is not None and process.poll() is None:
                process.terminate()
            AssistantController._bridge_proc = None
            _close_job(AssistantController._bridge_job)
            AssistantController._bridge_job = None
            return False
        except Exception as error:
            Logger.log("w", "Could not start TopOpt bridge: %s", str(error))
            self._bridge_start_error = "Could not start TopOpt bridge: %s" % str(error)
            process = AssistantController._bridge_proc
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except Exception:
                    pass
            AssistantController._bridge_proc = None
            _close_job(AssistantController._bridge_job)
            AssistantController._bridge_job = None
            return False

    @pyqtSlot(str, str)
    def saveOptimizationSettings(self, url, token):
        self._preferences.setValue("ai_assistant/optimization_url", url.strip())
        self._preferences.setValue("ai_assistant/optimization_token", token.strip())
        self._optimization = {"checking": False, "capabilities": None,
                              "templates": [], "error": "Настройки сохранены; запустите проверку."}
        self.optimizationChanged.emit()

    @pyqtSlot()
    def refreshOptimization(self):
        self._begin_optimization_check(True, True)

    @pyqtSlot()
    def refreshOptimizationStatus(self):
        self._begin_optimization_check(False, False)

    @pyqtSlot()
    def startOptimizationBridge(self):
        self._begin_optimization_check(True, True)

    @pyqtSlot()
    def stopOptimizationBridge(self):
        process = AssistantController._bridge_proc
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        _close_job(AssistantController._bridge_job)
        AssistantController._bridge_job = None
        if process is None:
            connection = self._read_bridge_connection()
            self._terminate_bridge_pid(connection.get("pid"))
        AssistantController._bridge_proc = None
        self._remove_bridge_connection()
        self._optimization = {"checking": False, "capabilities": None,
                              "templates": [], "error": "Служба TopOpt остановлена."}
        self.optimizationChanged.emit()

    def _begin_optimization_check(self, deep, auto_start=False):
        if self._optimization["checking"]:
            return
        self._optimization["checking"] = True
        self._optimization["error"] = ""
        self.optimizationChanged.emit()
        thread = threading.Thread(target=self._check_optimization,
                                  args=(deep, auto_start))
        thread.daemon = True
        thread.start()

    def _check_optimization(self, deep, auto_start=False):
        try:
            if auto_start and not self._ensure_bridge_started():
                raise RuntimeError(
                    self._bridge_start_error or "Could not start the local TopOpt bridge"
                )
            capabilities = self._registry.call(
                "get_optimization_capabilities", {"deep": bool(deep)}
            )
            templates = self._registry.call("list_optimization_templates", {})
            template_items = templates.get("templates", [])
            capabilities = map_optimization_status(
                capabilities, template_items
            )
            self._app.callLater(self._finish_optimization_check, capabilities,
                                template_items, "")
        except Exception as error:
            self._app.callLater(self._finish_optimization_check, None, [], str(error))

    def _finish_optimization_check(self, capabilities, templates, error):
        self._optimization = {"checking": False, "capabilities": capabilities,
                              "templates": templates, "error": error}
        self.optimizationChanged.emit()

    @pyqtSlot()
    def refreshEconomics(self):
        try:
            self._economics["slicing"] = self._registry.call("get_slicing_status", {})
            self._economics["quote"] = None
            self._economics["error"] = ""
        except Exception as error:
            self._economics["slicing"] = None
            self._economics["quote"] = None
            self._economics["error"] = str(error)
        self.economicsChanged.emit()

    @pyqtSlot(str)
    def calculateEconomics(self, payload):
        try:
            arguments = json.loads(payload)
            self._economics["quote"] = self._registry.call("estimate_print_cost", arguments)
            self._economics["slicing"] = self._registry.call("get_slicing_status", {})
            self._economics["error"] = ""
        except Exception as error:
            self._economics["quote"] = None
            self._economics["error"] = str(error)
        self.economicsChanged.emit()

    @pyqtSlot(str)
    def captureEconomics(self, payload):
        try:
            arguments = json.loads(payload)
            snapshot = self._registry.call("capture_cost_variant", arguments)
            self._economics[snapshot["slot"]] = snapshot
            self._economics["quote"] = snapshot["quote"]
            self._economics["slicing"] = self._registry.call("get_slicing_status", {})
            if self._economics["baseline"] and self._economics["candidate"]:
                self._economics["comparison"] = self._registry.call(
                    "compare_cost_variants", {})
            self._economics["error"] = ""
        except Exception as error:
            self._economics["error"] = str(error)
        self.economicsChanged.emit()

    @pyqtProperty(int, constant=True)
    def mcpPort(self):
        return self._mcp_server.port

    @pyqtProperty(str, constant=True)
    def mcpToken(self):
        return self._mcp_server.token

    @pyqtSlot(str, str, str)
    def saveSettings(self, base_url, api_key, model):
        self._preferences.setValue("ai_assistant/base_url", base_url.strip())
        self._preferences.setValue("ai_assistant/api_key", api_key.strip())
        self._preferences.setValue("ai_assistant/model", model.strip())
        self.settingsChanged.emit()
        self._append("system", "Настройки подключения сохранены.", "status")

    @pyqtSlot("QVariantList")
    def setAttachments(self, urls):
        result = []
        for value in urls:
            url = value if isinstance(value, QUrl) else QUrl(str(value))
            path = url.toLocalFile()
            if path and os.path.isfile(path):
                result.append(path)
        self._attachments = result[:8]
        self._attachment_error = "" if result else "Не удалось прикрепить выбранный файл"
        self.attachmentChanged.emit()

    @pyqtSlot(str)
    def setAttachmentsJson(self, payload):
        try:
            urls = json.loads(payload)
            if not isinstance(urls, list):
                raise ValueError("Expected a list of file URLs")
            self.setAttachments(urls)
        except (ValueError, TypeError):
            self._attachments = []
            self._attachment_error = "Ошибка чтения выбранных файлов"
            self.attachmentChanged.emit()

    @pyqtSlot()
    def clearAttachments(self):
        self._attachments = []
        self._attachment_error = ""
        self.attachmentChanged.emit()

    @pyqtSlot(str, result=bool)
    def copyToClipboard(self, text):
        try:
            from PyQt5.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
                return True
        except Exception:
            Logger.logException("w", "Could not copy assistant text to clipboard")
        return False

    @pyqtSlot()
    def clearHistory(self):
        if self._busy:
            return
        self._messages = []
        self._history = []
        self._save_history()
        self.messagesChanged.emit()

    @pyqtSlot(str)
    def sendMessage(self, text):
        text = text.strip()
        if not text or self._busy:
            return
        attachments = list(self._attachments)
        self.clearAttachments()
        self._append("user", text, "message", attachments)
        self._set_busy(True)
        connection = (self.baseUrl, self.apiKey, self.model)
        history = list(self._history)
        thread = threading.Thread(target=self._run_agent,
                                  args=(text, attachments, connection, history))
        thread.daemon = True
        thread.start()

    @pyqtSlot(str, result=str)
    def executeJsonRpc(self, request_text):
        try:
            from .core.jsonrpc import JsonRpcDispatcher

            request = json.loads(request_text)
            response = JsonRpcDispatcher(self._registry).dispatch(request)
            return json.dumps(response, ensure_ascii=False)
        except Exception as error:
            return json.dumps({"error": str(error)}, ensure_ascii=False)

    def _run_agent(self, text, attachments, connection, history):
        try:
            client = ChatClient(*connection)
            agent = Agent(client, self._registry, self._on_agent_event)
            answer = agent.run(history, text, attachments)
            self._app.callLater(self._finish_success, text, answer)
        except Exception as error:
            Logger.logException("e", "AI Assistant request failed")
            self._app.callLater(self._finish_error, str(error))

    def _on_agent_event(self, kind, value):
        name = value.get("name", "tool")
        if kind == "tool_error":
            Logger.log("e", "AI Assistant tool %s failed: %s",
                       name, value.get("error", "unknown error"))
        elif kind == "tool_result" and name in ("save_project_3mf", "export_gcode"):
            result = value.get("result") or {}
            Logger.log(
                "i" if result.get("verified") else "w",
                "AI Assistant file tool %s result: verified=%s path=%s bytes=%s",
                name, str(result.get("verified")), str(result.get("path")),
                str(result.get("bytes")),
            )
        self._app.callLater(self._append, "tool", self._event_text(kind, value), kind)

    @staticmethod
    def _event_text(kind, value):
        name = value.get("name", "tool")
        if kind == "tool_start":
            return "Вызов %s: %s" % (
                name,
                json.dumps(value.get("arguments", {}), ensure_ascii=False),
            )
        if kind == "tool_result":
            result = value.get("result")
            if name in ("save_project_3mf", "export_gcode"):
                if not isinstance(result, dict) or result.get("verified") is not True:
                    return "%s не подтвердил запись: %s" % (
                        name, json.dumps(result, ensure_ascii=False)
                    )
                return "%s подтвердил файл: %s" % (
                    name, json.dumps(result, ensure_ascii=False)
                )
            return "%s выполнен: %s" % (name, json.dumps(result, ensure_ascii=False))
        return "%s: %s" % (name, value.get("error", "ошибка"))

    def _finish_success(self, user_text, answer):
        self._history.extend(
            [{"role": "user", "content": user_text}, {"role": "assistant", "content": answer}]
        )
        self._append("assistant", answer, "message")
        self._save_history()
        self._set_busy(False)

    def _finish_error(self, message):
        self._append("system", "Ошибка: " + message, "error")
        self._set_busy(False)

    def _append(self, role, text, kind="message", attachments=None):
        self._messages.append(
            {
                "role": role,
                "text": text,
                "kind": kind,
                "attachments": [os.path.basename(path) for path in (attachments or [])],
            }
        )
        self._messages = self._messages[-200:]
        self.messagesChanged.emit()

    def _set_busy(self, value):
        self._busy = value
        self.busyChanged.emit()

    def _save_history(self):
        self._preferences.setValue(
            "ai_assistant/history", json.dumps(self._history[-40:], ensure_ascii=False)
        )

    def _load_history(self):
        try:
            history = json.loads(self._preferences.getValue("ai_assistant/history") or "[]")
            if isinstance(history, list):
                self._history = history[-40:]
                for message in self._history:
                    self._messages.append(
                        {
                            "role": message.get("role", "assistant"),
                            "text": message.get("content", ""),
                            "kind": "message",
                            "attachments": [],
                        }
                    )
        except Exception:
            self._history = []
