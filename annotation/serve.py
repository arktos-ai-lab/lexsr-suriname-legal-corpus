"""
Local server for the LexSR verification tool.

    python annotation/serve.py            # then open http://127.0.0.1:8765

Serves annotate.html and the task files, and appends every verdict to
annotation/labels/<task>.jsonl the moment it is given (flushed and fsynced),
so closing the browser or a crash loses nothing. Re-labelling an item
appends a new line; the latest line per item wins.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
TASKS = HERE / "tasks"
LABELS = HERE / "labels"
RAW = HERE.parent / "data" / "raw"
VERDICTS = {"correct", "wrong", "unsure", "skip"}


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def task_names() -> list:
    return sorted(p.stem for p in TASKS.glob("*.jsonl"))


def latest_labels(task: str) -> dict:
    out = {}
    for row in read_jsonl(LABELS / f"{task}.jsonl"):
        out[row["item_id"]] = row
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, (HERE / "annotate.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/tasks":
            summary = []
            for t in task_names():
                ids = {it["id"] for it in read_jsonl(TASKS / f"{t}.jsonl")}
                n = len(ids)
                # labels of items no longer in the task (kept for the record) are not counted
                done = sum(1 for i, r in latest_labels(t).items() if i in ids and r["verdict"] != "skip")
                summary.append({"name": t, "items": n, "done": done})
            self._json(summary)
        elif path.startswith("/api/task/"):
            t = path.rsplit("/", 1)[-1]
            if t not in task_names():
                return self._json({"error": "unknown task"}, 404)
            self._json({"items": read_jsonl(TASKS / f"{t}.jsonl"), "labels": latest_labels(t)})
        elif path.startswith("/raw/"):
            # source files (PDF, HTML) under data/raw only, for checking OCR against the original
            f = (RAW / unquote(path[len("/raw/"):])).resolve()
            if RAW.resolve() not in f.parents or not f.is_file():
                return self._json({"error": "not found"}, 404)
            ctype = {".pdf": "application/pdf", ".html": "text/html; charset=utf-8",
                     ".htm": "text/html; charset=utf-8"}.get(f.suffix.lower(), "application/octet-stream")
            self._send(200, f.read_bytes(), ctype)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/api/label":
            return self._json({"error": "not found"}, 404)
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if data.get("task") not in task_names() or data.get("verdict") not in VERDICTS \
                or not data.get("annotator") or not data.get("item_id"):
            return self._json({"error": "bad label"}, 400)
        row = {
            "item_id": data["item_id"],
            "verdict": data["verdict"],
            "note": data.get("note", ""),
            "annotator": data["annotator"],
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        LABELS.mkdir(exist_ok=True)
        with open(LABELS / f"{data['task']}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._json({"ok": True, "label": row})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if not task_names():
        raise SystemExit("No tasks found. Run: python annotation/export_tasks.py first.")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"LexSR verification tool: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
