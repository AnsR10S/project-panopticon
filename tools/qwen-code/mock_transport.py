\"\"\"Mock transport server for testing local Qwen client handshakes.\"\"\"

import http.server
import json
import logging
import socketserver
import sys

PORT = 8080
logger = logging.getLogger("mock_transport")


class MockHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {}

        if self.path == "/v1/chat/completions":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {
                "id": "mock-qwen-001",
                "object": "chat.completion",
                "model": "qwen-code-mock",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "REASONING: Maintaining stable margins.\nPRICE: 11.25",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 18, "total_tokens": 138},
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        elif self.path == "/v1/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ready", "provider": "mock_transport"}')
        else:
            self.send_response(404)
            self.end_headers()


def serve(port: int = PORT):
    with socketserver.TCPServer(("", port), MockHandler) as httpd:
        print(f"Mock transport listening on port {port}")
        httpd.serve_forever()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    serve(p)
