"""Loopback-only HTTP transport for initialize, tools/list and tools/call."""

import json
import threading
import uuid

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
except ImportError:  # pragma: no cover - retained for older embedded builds.
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn


class _ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class McpHttpServer(object):
    def __init__(self, dispatcher):
        self._dispatcher = dispatcher
        self._server = None
        self._thread = None
        self._token = uuid.uuid4().hex

    @property
    def token(self):
        return self._token

    @property
    def port(self):
        return self._server.server_port if self._server else 0

    def start(self):
        if self._server:
            return self.port
        dispatcher = self._dispatcher
        token = self._token

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/health":
                    self.send_error(404)
                    return
                self._send(200, {"status": "ok", "service": "ste-ai-assistant-mcp"})

            def do_POST(self):
                if self.path != "/mcp":
                    self.send_error(404)
                    return
                if self.headers.get("X-Session-Token") != token:
                    self._send(403, {"error": "MCP token required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 1024 * 1024:
                        raise ValueError("Invalid request size")
                    request = json.loads(self.rfile.read(length).decode("utf-8"))
                    response = dispatcher.dispatch(request)
                    self._send(200, response if response is not None else {})
                except Exception as error:
                    self._send(400, {"error": str(error)})

            def _send(self, status, value):
                body = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format_string, *args):
                return

        self._server = _ThreadedServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever)
        self._thread.daemon = True
        self._thread.start()
        return self.port

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._thread = None
