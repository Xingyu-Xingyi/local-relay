# 用途：本地起一个只做转发和记录的代理，不伪造凭据；上游要什么就照传什么
import argparse
import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SKIP_HEADERS = {"host", "connection", "content-length", "accept-encoding"}
SECRET_HEADERS = {"authorization", "x-api-key", "cookie"}


def mask(name, value):
    if name.lower() in SECRET_HEADERS:
        return value[:6] + "…" if len(value) > 6 else "***"
    return value


class Handler(BaseHTTPRequestHandler):
    upstream = ""
    log_path = ""

    def _proxy(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {}
        for name, value in self.headers.items():
            if name.lower() in SKIP_HEADERS:
                continue
            headers[name] = value

        url = self.upstream.rstrip("/") + self.path
        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)

        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status, payload = resp.status, resp.read()
                resp_headers = dict(resp.headers.items())
        except urllib.error.HTTPError as e:
            status, payload = e.code, e.read()
            resp_headers = dict(e.headers.items()) if e.headers else {}
        except Exception as e:  # 连不上之类，记成 502，不要当成上游的判定
            status, payload = 502, json.dumps({"relay_error": str(e)}).encode()
            resp_headers = {"Content-Type": "application/json"}

        elapsed = int((time.time() - started) * 1000)

        # 分类：4xx 是上游在判定，5xx 是链路上出的事，两类不混
        if 400 <= status < 500:
            bucket = "rejected_by_upstream"
        elif status >= 500:
            bucket = "link_error"
        else:
            bucket = "ok"

        record = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "method": self.command,
            "path": self.path,
            "status": status,
            "bucket": bucket,
            "elapsed_ms": elapsed,
            "req_headers": {k: mask(k, v) for k, v in headers.items()},
            "resp_headers": resp_headers,
            "resp_preview": payload[:400].decode("utf-8", "replace"),
        }
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        self.send_response(status)
        for k, v in resp_headers.items():
            if k.lower() in {"content-length", "transfer-encoding", "connection"}:
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _proxy

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--port", type=int, default=8812)
    ap.add_argument("--log", default="log.jsonl")
    args = ap.parse_args()

    Handler.upstream = args.upstream
    Handler.log_path = args.log
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"relay on http://127.0.0.1:{args.port} → {args.upstream}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
