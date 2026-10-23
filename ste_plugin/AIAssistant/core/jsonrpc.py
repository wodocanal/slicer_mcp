"""Small MCP-compatible JSON-RPC dispatcher for the Python bundled with STE."""

from .errors import ToolError


class JsonRpcDispatcher(object):
    def __init__(self, tools):
        self._tools = tools

    def dispatch(self, request):
        request_id = request.get("id")
        if request.get("jsonrpc") != "2.0":
            return self._error(request_id, -32600, "Expected JSON-RPC 2.0")
        try:
            method = request.get("method")
            params = request.get("params") or {}
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "ste-ai-assistant", "version": "0.1.0"},
                }
            elif method == "notifications/initialized":
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self._tools.schemas()}
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                value = self._tools.call(name, arguments)
                result = {
                    "content": [{"type": "text", "text": self._tools.to_json(value)}],
                    "structuredContent": value,
                    "isError": False,
                }
            else:
                return self._error(request_id, -32601, "Method not found: %s" % method)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (ToolError, ValueError, KeyError) as error:
            return self._error(request_id, -32000, str(error))
        except Exception as error:
            return self._error(request_id, -32603, "Internal error: %s" % error)

    @staticmethod
    def _error(request_id, code, message):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
