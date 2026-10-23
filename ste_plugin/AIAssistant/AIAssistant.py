import os

from PyQt5.QtCore import QObject

from UM.Application import Application
from UM.Extension import Extension
from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry

from .AssistantController import AssistantController
from .core.jsonrpc import JsonRpcDispatcher
from .core.mcp_server import McpHttpServer
from .core.tool_registry import ToolRegistry
from .ste.adapter import SteSlicerAdapter
from .ste.qt_executor import QtExecutor
from .ste.tools import build_registry


class AIAssistant(QObject, Extension):
    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        Extension.__init__(self)
        self.setMenuName("AI Assistant")
        self.addMenuItem("Открыть чат", self.showAssistant)
        self.addMenuItem("Настройки", self.showSettings)
        self._app = Application.getInstance()
        self._window = None
        self._controller = None
        self._mcp_server = None
        self._initialize_core()

    def _initialize_core(self):
        executor = QtExecutor(self._app)
        adapter = SteSlicerAdapter(self._app, executor)
        registry = build_registry(ToolRegistry(), adapter)
        self._mcp_server = McpHttpServer(JsonRpcDispatcher(registry))
        try:
            port = self._mcp_server.start()
            Logger.log("i", "AI Assistant MCP listening on 127.0.0.1:%d", port)
        except Exception:
            Logger.logException("e", "Could not start AI Assistant MCP endpoint")
        self._controller = AssistantController(self._app, registry, self._mcp_server, self)

    def showAssistant(self):
        if self._window is None:
            path = os.path.join(
                PluginRegistry.getInstance().getPluginPath(self.getPluginId()), "Assistant.qml"
            )
            self._window = self._app.createQmlComponent(path, {"manager": self._controller})
        if self._window:
            self._window.show()
            self._window.raise_()

    def showSettings(self):
        self.showAssistant()
        if self._window:
            try:
                self._window.showSettings()
            except Exception:
                Logger.logException("w", "Could not switch AI Assistant to settings tab")
