import ast
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "ste_plugin" / "AIAssistant"
sys.path.insert(0, str(PLUGIN))

from core.jsonrpc import JsonRpcDispatcher
from core.mcp_server import McpHttpServer
from core.tool_registry import ToolRegistry


def registry():
    value = ToolRegistry()
    value.add(
        "add",
        "Add numbers",
        {"a": {"type": "number"}, "b": {"type": "number"}},
        ["a", "b"],
        lambda args: {"sum": args["a"] + args["b"]},
    )
    return value


def test_all_plugin_python_is_python35_compatible():
    for path in PLUGIN.rglob("*.py"):
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, 5))


def test_jsonrpc_initialize_list_and_call():
    dispatcher = JsonRpcDispatcher(registry())
    initialized = dispatcher.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert initialized["result"]["serverInfo"]["name"] == "ste-ai-assistant"
    listed = dispatcher.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert listed["result"]["tools"][0]["name"] == "add"
    called = dispatcher.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "add", "arguments": {"a": 2, "b": 4}},
        }
    )
    assert called["result"]["structuredContent"] == {"sum": 6}
    assert json.loads(called["result"]["content"][0]["text"]) == {"sum": 6}


def test_jsonrpc_rejects_unknown_arguments():
    response = JsonRpcDispatcher(registry()).dispatch(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "add", "arguments": {"a": 1, "b": 2, "shell": "no"}},
        }
    )
    assert response["error"]["code"] == -32000


def test_loopback_http_transport():
    from urllib.request import Request, urlopen

    server = McpHttpServer(JsonRpcDispatcher(registry()))
    port = server.start()
    try:
        assert server._server.server_address[0] == "127.0.0.1"
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        ).encode()
        response = urlopen(
            Request(
                f"http://127.0.0.1:{port}/mcp",
                data=body,
                headers={"Content-Type": "application/json"},
            )
        )
        assert json.loads(response.read())["result"]["tools"][0]["name"] == "add"
    finally:
        server.stop()


def test_qml_uses_embedded_dialog_and_exposes_tool_activity():
    qml = (PLUGIN / "Assistant.qml").read_text()
    assert "UM.Dialog" in qml
    assert "Qt.NonModal" in qml
    assert 'modelData.role === "tool"' in qml
    assert "manager.sendMessage" in qml
