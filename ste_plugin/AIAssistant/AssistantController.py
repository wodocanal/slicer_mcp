import json
import os
import threading

from PyQt5.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Logger import Logger

from .core.llm import Agent, ChatClient


DEFAULT_BASE_URL = "https://api.timeweb.ai/v1"
DEFAULT_MODEL = "dashscope/qwen3.8-flash"


class AssistantController(QObject):
    messagesChanged = pyqtSignal()
    busyChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    attachmentChanged = pyqtSignal()
    economicsChanged = pyqtSignal()
    optimizationChanged = pyqtSignal()
    insightsChanged = pyqtSignal()

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
        self._insights = {"busy": False, "risk_map": None, "failure_cost": None,
                          "error": ""}
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

    @pyqtProperty(str, notify=insightsChanged)
    def insightsJson(self):
        return json.dumps(self._insights, ensure_ascii=False)

    @pyqtSlot(str)
    def calculateRiskMap(self, payload):
        if self._insights["busy"]:
            return
        self._insights["busy"] = True
        self._insights["error"] = ""
        self.insightsChanged.emit()
        thread = threading.Thread(target=self._calculate_risk_map, args=(payload,))
        thread.daemon = True
        thread.start()

    def _calculate_risk_map(self, payload):
        try:
            result = self._registry.call("analyze_geometry_risk_map", json.loads(payload))
            self._app.callLater(self._finish_insight, "risk_map", result, "")
        except Exception as error:
            self._app.callLater(self._finish_insight, "risk_map", None, str(error))

    @pyqtSlot(str)
    def calculateFailureCost(self, payload):
        if self._insights["busy"]:
            return
        self._insights["busy"] = True
        self._insights["error"] = ""
        self.insightsChanged.emit()
        thread = threading.Thread(target=self._calculate_failure_cost, args=(payload,))
        thread.daemon = True
        thread.start()

    def _calculate_failure_cost(self, payload):
        try:
            result = self._registry.call(
                "analyze_failure_cost_scenarios", json.loads(payload)
            )
            self._app.callLater(self._finish_insight, "failure_cost", result, "")
        except Exception as error:
            self._app.callLater(self._finish_insight, "failure_cost", None, str(error))

    def _finish_insight(self, key, value, error):
        self._insights["busy"] = False
        self._insights[key] = value
        self._insights["error"] = error
        self.insightsChanged.emit()

    @pyqtSlot(str, str)
    def saveOptimizationSettings(self, url, token):
        self._preferences.setValue("ai_assistant/optimization_url", url.strip())
        self._preferences.setValue("ai_assistant/optimization_token", token.strip())
        self._optimization = {"checking": False, "capabilities": None,
                              "templates": [], "error": "Настройки сохранены; запустите проверку."}
        self.optimizationChanged.emit()

    @pyqtSlot()
    def refreshOptimization(self):
        if self._optimization["checking"]:
            return
        self._optimization["checking"] = True
        self._optimization["error"] = ""
        self.optimizationChanged.emit()
        thread = threading.Thread(target=self._check_optimization)
        thread.daemon = True
        thread.start()

    def _check_optimization(self):
        try:
            capabilities = self._registry.call("get_optimization_capabilities", {"deep": True})
            templates = self._registry.call("list_optimization_templates", {})
            self._app.callLater(self._finish_optimization_check, capabilities,
                                templates.get("templates", []), "")
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
        except Exception as error:
            Logger.logException("w", "Failed to copy text to clipboard: %s" % error)
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
            return "%s выполнен: %s" % (name, json.dumps(value.get("result"), ensure_ascii=False))
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
