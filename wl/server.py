"""A tiny local-only HTTP server: JSON API + the calendar UI files.

Binds to 127.0.0.1 by design -- the database never leaves the machine.
"""
import csv
import io
import json
import mimetypes
import os
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
BLOCK_PATH = re.compile(r"/api/blocks/(\d+)")
_local = threading.local()


def _con():
    if getattr(_local, "con", None) is None:
        _local.con = db.connect()
    return _local.con


class Handler(BaseHTTPRequestHandler):
    server_version = "worklog/1.0"

    def log_message(self, fmt, *args):
        pass  # keep the log clean; errors still surface as responses

    # -- plumbing ----------------------------------------------------------
    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def _file(self, name):
        path = os.path.normpath(os.path.join(WEB_DIR, name))
        if not path.startswith(WEB_DIR) or not os.path.isfile(path):
            return self._send(404, "not found", "text/plain")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _local_only(self):
        """Refuse anything not addressed to, and sent from, this machine's own UI.

        Without this any web page you visit could post blocks to localhost.
        """
        port = self.server.server_address[1]
        ours = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        origin = self.headers.get("Origin")
        ok = self.headers.get("Host") in ours and (
            not origin or urllib.parse.urlparse(origin).netloc in ours)
        if not ok:
            self._send(403, "forbidden", "text/plain")
        return ok

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        if not self._local_only():
            return
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        one = lambda k, d=None: (q.get(k) or [d])[0]
        path = url.path
        try:
            if path in ("/", "/index.html"):
                return self._file("index.html")
            if path.startswith("/static/"):
                return self._file(path[len("/static/"):])

            if path == "/api/ping":
                return self._json({"app": "worklog"})

            if path == "/api/overview":
                return self._json(db.overview(_con()))

            if path == "/api/blocks":
                return self._json({
                    "blocks": db.list_blocks(_con(), one("start", "0000-01-01"),
                                             one("end", "9999-12-31")),
                    "projects": db.project_slots(_con()),
                })

            if path == "/api/export.csv":
                buf = io.StringIO()
                out = csv.writer(buf)
                out.writerow(["day", "start", "end", "minutes", "title", "project",
                              "rating", "notes"])
                for b in db.list_blocks(_con(), one("start", "0000-01-01"),
                                        one("end", "9999-12-31")):
                    out.writerow([b["day"], db.fmt_min(b["start_min"]), db.fmt_min(b["end_min"]),
                                  b["end_min"] - b["start_min"], b["title"], b["project"],
                                  b["rating"] or "", b["notes"]])
                return self._send(200, buf.getvalue(), "text/csv",
                                  {"Content-Disposition": 'attachment; filename="worklog.csv"'})

            return self._send(404, "not found", "text/plain")
        except Exception as exc:  # never take the UI down over one bad request
            return self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def _write(self, method):
        if not self._local_only():
            return
        path = urllib.parse.urlparse(self.path).path
        body = self._body()
        one = BLOCK_PATH.fullmatch(path)
        try:
            if method == "POST" and path == "/api/blocks":
                return self._json(db.create_block(_con(), body), 201)
            if method == "PUT" and one:
                block = db.update_block(_con(), int(one.group(1)), body)
                return self._json(block) if block else self._json({"error": "no such block"}, 404)
            if method == "DELETE" and one:
                gone = db.delete_block(_con(), int(one.group(1)))
                return self._json({"ok": True}) if gone else self._json({"error": "no such block"}, 404)
            if method == "POST" and path == "/api/goal":
                hours = float(body.get("hours"))
                if not 0 < hours <= 24:
                    raise db.Invalid("goal must be between 0 and 24 hours")
                db.set_setting(_con(), "goal_hours", hours)
                return self._json({"ok": True})
            return self._send(404, "not found", "text/plain")
        except db.Overlap as exc:
            return self._json({"error": str(exc)}, 409)
        except (db.Invalid, TypeError, ValueError) as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            return self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def do_POST(self):
        self._write("POST")

    def do_PUT(self):
        self._write("PUT")

    def do_DELETE(self):
        self._write("DELETE")


def serve(host=None, port=None):
    host = host or config.SERVER_HOST
    port = int(port or config.SERVER_PORT)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
