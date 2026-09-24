"""The cutover must authenticate the existing private Portfolio health probe."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import httpx
import pytest

from jobhunt_core.document_cutover import require_freeze


def test_portfolio_freeze_probe_uses_operator_token(monkeypatch):
    received = []

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            token = self.headers.get("Authorization")
            received.append(token)
            authenticated = token == "Bearer synthetic-cutover-admin"
            body = json.dumps({"checks": {"schedulers": {"writes": "frozen"}}}).encode()
            self.send_response(200 if authenticated else 401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "DOCUMENT_FREEZE_URL", f"http://127.0.0.1:{server.server_port}/health/deep"
    )
    try:
        monkeypatch.delenv("DOCUMENT_FREEZE_TOKEN", raising=False)
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(require_freeze("portfolio"))
        monkeypatch.setenv("DOCUMENT_FREEZE_TOKEN", "synthetic-cutover-admin")
        asyncio.run(require_freeze("portfolio"))
        assert received == [None, "Bearer synthetic-cutover-admin"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
