import json
import os
import threading

from PyQt5.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Logger import Logger

from .core.llm import Agent, ChatClient


class AssistantController(QObject):
    messagesChanged = pyqtSignal()
    busyChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    attachmentChanged = pyqtSignal()

    def __init__(self, application, registry, mcp_server, parent=None):
        QObject.__init__(self, parent)
        self._app = application
        self._registry = registry
        self._mcp_server = mcp_server
        self._messages = []
        self._history = []
        self._attachments = []
        self._busy = False
        preferences = application.getPreferences()
        preferences.addPreference("ai_assistant/base_url", "")
        preferences.addPreference("ai_assistant/api_key", "")
        preferences.addPreference("ai_assistant/model", "")
        preferences.addPreference("ai_assistant/history", "[]")
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
            return "Файлы не выбраны"
        return ", ".join(os.path.basename(path) for path in self._attachments)

    @pyqtProperty(int, constant=True)
    def mcpPort(self):
        return self._mcp_server.port

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
        self.attachmentChanged.emit()

    @pyqtSlot()
    def clearAttachments(self):
        self._attachments = []
        self.attachmentChanged.emit()

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
        thread = threading.Thread(target=self._run_agent, args=(text, attachments))
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

    def _run_agent(self, text, attachments):
        try:
            client = ChatClient(self.baseUrl, self.apiKey, self.model)
            agent = Agent(client, self._registry, self._on_agent_event)
            answer = agent.run(list(self._history), text, attachments)
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
