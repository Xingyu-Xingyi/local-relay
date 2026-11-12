# 用途：离线自测 relay —— 起一个假上游，看转发、状态码分类和日志对不对
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import relay as relay_mod

UP_PORT, RELAY_PORT = 18811, 18812
LOG = "selftest.jsonl"


class Upstream(BaseHTTPRequestHandler):
    # 假上游：/ok 放行，其它一律当没凭据打回
    def do_GET(self):
        if self.path.startswith("/ok"):
            code, body = 200, b'{"ok":true}'
        elif self.path.startswith("/rejected"):
            code, body = 401, b'{"error":"missing_or_invalid_credential"}'
        else:
            code, body = 500, b'{"error":"boom"}'
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def hit(path, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{RELAY_PORT}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    up = ThreadingHTTPServer(("127.0.0.1", UP_PORT), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()

    relay_mod.Handler.upstream = f"http://127.0.0.1:{UP_PORT}"
    relay_mod.Handler.log_path = LOG
    open(LOG, "w").close()
    rly = ThreadingHTTPServer(("127.0.0.1", RELAY_PORT), relay_mod.Handler)
    threading.Thread(target=rly.serve_forever, daemon=True).start()

    results = []
    s, b = hit("/ok")
    results.append(("放行的请求原样透传", s == 200 and b'{"ok":true}' in b, s, b[:60]))

    s, b = hit("/rejected", {"Authorization": "Bearer made-up-token"})
    results.append(("没凭据被上游打回，同样透传 401", s == 401, s, b[:60]))

    s, b = hit("/rejected")
    results.append(("一个头都不带也是 401，不是我这边伪造的", s == 401, s, b[:60]))

    lines = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]
    buckets = [r["bucket"] for r in lines]
    results.append(("日志按状态码分了类", buckets == ["ok", "rejected_by_upstream", "rejected_by_upstream"], buckets, ""))

    leaked = [r for r in lines if "made-up-token" in json.dumps(r, ensure_ascii=False)]
    results.append(("日志里凭据被遮了", not leaked, len(leaked), ""))

    print(f"记录 {len(lines)} 条，键：{sorted(lines[0].keys())}")
    ok = True
    for name, passed, got, extra in results:
        print(("PASS " if passed else "FAIL ") + name, "→", got, extra)
        ok = ok and passed
    print("整体：", "通过" if ok else "有失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
