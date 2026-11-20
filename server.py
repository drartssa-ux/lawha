#!/usr/bin/env python3
# لوحة التحدي — السيرفر
# يشتغل على جهازك (نفس الواي فاي) أو على موقع (مثل Render). بايثون 3 بس، بدون مكتبات.
# كل شاشة كبيرة تفتح "غرفة" برمز خاص، فتقدر مجموعات كثيرة تلعب بنفس الوقت.
import http.server, json, os, random, socket, sys, threading, time, webbrowser
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8000))
ON_SERVER = bool(os.environ.get("PORT"))
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")
MAX_ROOMS = 2000
ROOM_TTL = 6 * 3600

cond = threading.Condition()
ROOMS = {}


def new_room():
    for _ in range(200):
        code = str(random.randint(1000, 9999))
        if code not in ROOMS:
            break
    else:
        code = str(random.randint(10000, 99999))
    key = "%012x" % random.getrandbits(48)
    ROOMS[code] = {"v": 0, "host": None, "priv": None, "key": key, "players": {},
                   "cmds": [], "cid": 0, "seq": 0, "touched": time.time()}
    return code


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def bump(r):
    r["v"] += 1
    r["touched"] = time.time()
    cond.notify_all()


def snapshot(r, key=None):
    d = {"v": r["v"], "host": r["host"],
         "players": [{"pid": k, "team": p.get("team"), "buzz": p.get("buzz"), "seq": p.get("seq", 0)}
                     for k, p in r["players"].items()]}
    if key and key == r["key"]:
        d["priv"] = r["priv"]
        d["cmds"] = r["cmds"][-30:]
    return json.dumps(d, ensure_ascii=False)


def touch(r, pid):
    if pid and pid in r["players"]:
        r["players"][pid]["seen"] = time.time()


def janitor():
    while True:
        time.sleep(3)
        with cond:
            now = time.time()
            for code in list(ROOMS):
                r = ROOMS[code]
                if now - r["touched"] > ROOM_TTL:
                    del ROOMS[code]
                    continue
                gone = [k for k, p in r["players"].items() if now - p.get("seen", 0) > 25]
                for k in gone:
                    del r["players"][k]
                if gone:
                    bump(r)


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def chunk(self, b):
        self.wfile.write(("%X\r\n" % len(b)).encode() + b + b"\r\n")
        self.wfile.flush()

    def send_body(self, code, body, ctype="application/json; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def base_url(self):
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        hn = host.split(":")[0]
        if hn in ("localhost", "127.0.0.1", "0.0.0.0", "") and not ON_SERVER:
            return "http://%s:%d/" % (lan_ip(), PORT)
        proto = self.headers.get("X-Forwarded-Proto") or ("https" if ON_SERVER else "http")
        return "%s://%s/" % (proto.split(",")[0].strip(), host)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        g = lambda k: (qs.get(k) or [None])[0]
        if u.path in ("/", "/index.html"):
            with open(INDEX, "rb") as f:
                return self.send_body(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/healthz":
            return self.send_body(200, "ok", "text/plain")
        if u.path == "/api/info":
            return self.send_body(200, json.dumps({"url": self.base_url()}))
        room, pid, key = g("room"), g("pid"), g("key")
        if u.path == "/api/state":
            with cond:
                r = ROOMS.get(room)
                if not r:
                    return self.send_body(404, '{"error":"room"}')
                touch(r, pid)
                data = snapshot(r, key)
            return self.send_body(200, data)
        if u.path == "/api/events":
            with cond:
                if room not in ROOMS:
                    return self.send_body(404, '{"error":"room"}')
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.close_connection = True
            last = -1
            try:
                while True:
                    with cond:
                        r = ROOMS.get(room)
                        if not r:
                            break
                        if r["v"] == last:
                            cond.wait(timeout=10)
                            r = ROOMS.get(room)
                            if not r:
                                break
                        touch(r, pid)
                        data = snapshot(r, key) if r["v"] != last else None
                        v = r["v"]
                    if data is not None:
                        self.chunk(("data: %s\n\n" % data).encode("utf-8"))
                        last = v
                    else:
                        self.chunk(b": ping\n\n")
            except Exception:
                pass
            return
        self.send_body(404, "not found", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64000:
            return self.send_body(413, '{"error":"big"}')
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self.send_body(400, '{"error":"json"}')
        path = urlparse(self.path).path
        with cond:
            if path == "/api/room":  # الشاشة الكبيرة تفتح غرفة (أو ترجع لغرفتها)
                code, key = str(body.get("room") or ""), body.get("key")
                r = ROOMS.get(code)
                if not (r and key == r["key"]):
                    if len(ROOMS) >= MAX_ROOMS:
                        return self.send_body(503, '{"error":"full"}')
                    code = new_room()
                    r = ROOMS[code]
                r["touched"] = time.time()
                return self.send_body(200, json.dumps({"room": code, "key": r["key"]}))
            r = ROOMS.get(str(body.get("room") or ""))
            if not r:
                return self.send_body(404, '{"error":"room"}')
            if path == "/api/host":
                if body.get("key") != r["key"]:
                    return self.send_body(403, '{"error":"key"}')
                r["host"], r["priv"] = body.get("pub"), body.get("priv")
                bump(r)
            elif path == "/api/cmd":
                if body.get("key") != r["key"]:
                    return self.send_body(403, '{"error":"key"}')
                r["cid"] += 1
                c = {k: v for k, v in body.items() if k not in ("key", "room")}
                c["id"] = r["cid"]
                r["cmds"] = (r["cmds"] + [c])[-30:]
                bump(r)
            elif path == "/api/player":
                pid = str(body.get("pid") or "")[:20]
                if not pid:
                    return self.send_body(400, '{"error":"pid"}')
                if pid not in r["players"] and len(r["players"]) >= 60:
                    return self.send_body(503, '{"error":"full"}')
                p = r["players"].setdefault(pid, {})
                p["seen"] = time.time()
                if "team" in body:
                    t = body["team"]
                    p["team"] = str(t)[:20] if t else None
                    p["buzz"] = None
                if body.get("buzz"):
                    r["seq"] += 1
                    p["buzz"] = str(body["buzz"])[:30]
                    p["seq"] = r["seq"]
                bump(r)
            else:
                return self.send_body(404, '{"error":"path"}')
        self.send_body(200, "{}")


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    threading.Thread(target=janitor, daemon=True).start()
    try:
        srv = Server(("0.0.0.0", PORT), H)
    except OSError:
        print("المنفذ %d مستخدم. جرّب: python3 server.py 8080" % PORT)
        sys.exit(1)
    if ON_SERVER:
        print("Lawha server running on port %d" % PORT, flush=True)
    else:
        print("=" * 52)
        print("  لوحة التحدي شغّالة ✅")
        print("  افتح على الشاشة الكبيرة: http://localhost:%d" % PORT)
        print("  (الجوالات لازم تكون على نفس الواي فاي — صوّروا الـQR)")
        print("  للإيقاف: Ctrl+C أو سكّر هالنافذة")
        print("=" * 52)
        try:
            webbrowser.open("http://localhost:%d" % PORT)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nتم الإيقاف.")
