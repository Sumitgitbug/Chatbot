"""HTTP routes for the chat page, static UI files, and JSON API."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .chat_service import KNOWLEDGE_BASE, answer_question
from .config import CHAT_MODEL, GEMINI_API_KEY, HOST, PORT, WEB_DIR

STATIC_ASSETS = {
    "/chat-ui.css": ("chat-ui.css", "text/css; charset=utf-8"),
    "/chat-ui.js": ("chat-ui.js", "text/javascript; charset=utf-8"),
    "/widget.js": ("widget.js", "text/javascript; charset=utf-8"),
}


class ChatHandler(BaseHTTPRequestHandler):
    """Serve the single-page interface and its chat API."""

    server_version = "RCBChat/1.0"

    def send_json(self, status, payload):
        """Write an API response as UTF-8 JSON."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, filename, content_type):
        """Serve a known UI asset from the components folder."""
        content = (WEB_DIR / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        """Serve the chat page, UI assets, or a basic health summary."""
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send_file("main.html", "text/html; charset=utf-8")
        if path in STATIC_ASSETS:
            filename, content_type = STATIC_ASSETS[path]
            return self._send_file(filename, content_type)
        if path == "/api/health":
            return self.send_json(200, {
                "status": "ok",
                "pages": len({chunk["url"] for chunk in KNOWLEDGE_BASE.chunks}),
                "chunks": KNOWLEDGE_BASE.ensure_fresh(),
                "vector_chunks": KNOWLEDGE_BASE.vector_count(),
                "model": CHAT_MODEL,
                "gemini_key_configured": bool(GEMINI_API_KEY),
            })
        return self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        """Validate a question and return the chatbot's answer and sources."""
        if urlparse(self.path).path != "/api/chat":
            return self.send_json(404, {"error": "Not found"})

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 12000:
                return self.send_json(400, {"error": "Please send a question of up to 5,000 characters."})
            payload = json.loads(self.rfile.read(length))
            question = str(payload.get("question", "")).strip()
            history = payload.get("history", [])
            if not isinstance(history, list):
                history = []
            history = [
                {"role": item.get("role"), "content": str(item.get("content", ""))[:2000]}
                for item in history[-8:]
                if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
            ]
            if not question:
                return self.send_json(400, {"error": "Question is required."})
            if len(question) > 5000:
                return self.send_json(400, {"error": "Question must be 5,000 characters or shorter."})
            return self.send_json(200, answer_question(question, history))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self.send_json(400, {"error": "Invalid JSON request."})
        except Exception as error:
            return self.send_json(500, {"error": str(error)})

    def log_message(self, format_string, *args):
        """Keep simple request logs in the terminal running the server."""
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def run_server():
    """Start the local threaded HTTP server and close it cleanly on Ctrl+C."""
    server = ThreadingHTTPServer((HOST, PORT), ChatHandler)
    page_count = len({chunk["url"] for chunk in KNOWLEDGE_BASE.chunks})
    print(f"RCB website chatbot listening at http://{HOST}:{PORT}")
    print(f"Loaded {page_count} pages / {len(KNOWLEDGE_BASE.chunks)} chunks. Gemini model: {CHAT_MODEL}")
    print("Starting automatic RCB crawl and vector-index refresh." if GEMINI_API_KEY else
          "Set GEMINI_API_KEY to enable crawling, embeddings, and generated answers.")
    from .ingest import start_refresh_scheduler
    start_refresh_scheduler()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping RCB chatbot.")
    finally:
        server.server_close()
