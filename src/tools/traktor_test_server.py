#!/usr/bin/env python3
"""
Standalone HTTP server — use this to verify Traktor is sending data at all.

Run BEFORE touching Track Kommander:
    python tools/traktor_test_server.py

Then load a track in Traktor. You should see the POST request printed here.
If nothing appears, Traktor is not loading the QML controller.

Default port is 8080 to match the original traktor-api-client defaults.
Pass a port as the first argument to use a different one:
    python tools/traktor_test_server.py 12345
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class DumpHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

        print(f"\n{'─' * 60}")
        print(f"POST  {self.path}")
        try:
            parsed = json.loads(body)
            print(json.dumps(parsed, indent=2))
        except Exception:
            print(body[:500])

    def do_GET(self):
        print(f"\n{'─' * 60}")
        print(f"GET   {self.path}")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"null")  # no command

    def log_message(self, *_):
        pass


port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
print(f"Traktor test server — listening on http://localhost:{port}")
print("Load a track in Traktor. Requests will appear here.")
print("Ctrl+C to stop.\n")
try:
    HTTPServer(("", port), DumpHandler).serve_forever()
except KeyboardInterrupt:
    print("\nStopped.")
