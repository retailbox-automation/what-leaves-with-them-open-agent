"""Tiny read-only docs API (stands in for a wiki / document store).

GET /docs        -> {"docs": [...]}           (what Zetaris registers as a REST table)
GET /docs/<id>   -> {"docs": [<one record>]}  (point read, same shape)

Usage: python3 -m zetaris_spike.docs_api <docs.json> [port]
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOCS = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/docs":
            body = {"docs": list(DOCS.values())}
        elif path.startswith("/docs/") and path[6:] in DOCS:
            body = {"docs": [DOCS[path[6:]]]}
        else:
            self.send_error(404)
            return
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        sys.stderr.write("docs_api %s\n" % (format % args))


if __name__ == "__main__":
    DOCS.update({d["id"]: d for d in json.load(open(sys.argv[1], encoding="utf-8"))["docs"]})
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 18090
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
