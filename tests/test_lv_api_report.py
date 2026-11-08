"""Testy integracyjne raportów `lv-api.sh` na lokalnym serwerze HTTP.

Sprawdzają, że raport `skipped` ponawia się na przejściowym 5xx i że błąd API
nie jest połykany (niezerowy kod wyjścia + treść odpowiedzi).
"""

from __future__ import annotations

import json
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "lv-executor"
    / "scripts"
    / "lv-api.sh"
)


class ReportHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []
    statuses: list[int] = []
    response_body: bytes = b"{}"

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).calls.append(
            {
                "path": self.path,
                "body": body.decode("utf-8"),
                "authorization": self.headers.get("Authorization"),
            }
        )
        status = type(self).statuses.pop(0) if type(self).statuses else 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def log_message(self, *_args):
        return


class ReportApiTest(unittest.TestCase):
    def start_server(self, statuses: list[int], response_body: bytes = b"{}"):
        ReportHandler.calls = []
        ReportHandler.statuses = list(statuses)
        ReportHandler.response_body = response_body
        server = ThreadingHTTPServer(("127.0.0.1", 0), ReportHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # LIFO: najpierw zatrzymujemy pętlę, potem wątek, na końcu zamykamy socket.
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        return server

    def run_skipped(self, port: int) -> subprocess.CompletedProcess:
        env = {
            "LV_API_URL": f"http://127.0.0.1:{port}",
            "LV_EXECUTOR_TOKEN": "test-token",
            "PATH": "/usr/bin:/bin",
        }
        return subprocess.run(
            ["bash", str(SCRIPT), "skipped", "bet-1", "odds_drift", "kurs 1.64 → 1.60"],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )

    def test_retries_transient_500_and_sends_terminal_report(self):
        server = self.start_server([500], response_body=b'{"error":"temporary"}')
        # Drugie wywołanie po 500 ma zwrócić 200; handler domyślnie zwraca 200,
        # gdy lista statusów się skończy.
        result = self.run_skipped(server.server_address[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(ReportHandler.calls), 2)
        call = ReportHandler.calls[0]
        self.assertEqual(call["path"], "/api/executor/queue/bet-1/confirm")
        self.assertEqual(call["authorization"], "Bearer test-token")
        payload = json.loads(call["body"])
        self.assertEqual(payload["success"], False)
        self.assertEqual(payload["aborted"], True)
        self.assertEqual(payload["reason"], "odds_drift")
        self.assertIn("1.64", payload["reasonDetail"])

    def test_4xx_is_not_retried_and_returns_body(self):
        server = self.start_server(
            [404],
            response_body=b'{"message":"Nie znaleziono zlecenia do potwierdzenia."}',
        )
        result = self.run_skipped(server.server_address[1])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(ReportHandler.calls), 1)
        self.assertIn("Nie znaleziono zlecenia", result.stdout)


if __name__ == "__main__":
    unittest.main()
