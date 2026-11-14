"""Shared helpers: settings, HTTP, multipart, state files, time."""
import json
import os
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
POSTS = ROOT / "docs" / "posts"
REFS = [ROOT / "references" / n for n in ("ref_front_smile.jpg", "ref_three_quarter.jpg", "ref_body.jpg")]
PAGES_BASE = "https://skyai2027-afk.github.io/sky-ai"
RAW_BASE = "https://raw.githubusercontent.com/skyai2027-afk/sky-ai/main"

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Jerusalem")
except Exception:  # Windows without tzdata: fall back to fixed +2
    TZ = timezone(timedelta(hours=2))


def load_env():
    """Read .env locally; on GitHub the values come from Secrets as env vars."""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def env(name, required=True):
    val = os.environ.get(name, "")
    if required and not val:
        raise RuntimeError(f"missing setting {name}")
    return val


def config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def now():
    return datetime.now(TZ)


def log(*args):
    print(f"[{now():%H:%M:%S}]", *args, flush=True)


# ---------- state files ----------
def read_state(name, default):
    p = STATE / name
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return default


def write_state(name, data):
    STATE.mkdir(exist_ok=True)
    (STATE / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- HTTP ----------
class HttpError(RuntimeError):
    def __init__(self, code, body):
        super().__init__(f"HTTP {code}: {body[:500]}")
        self.code, self.body = code, body


def http(url, data=None, headers=None, method=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, (e.read() or b"").decode("utf-8", "replace")) from None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def post_json(url, payload, headers=None, timeout=120):
    h = {"Content-Type": "application/json", **(headers or {})}
    return http(url, json.dumps(payload).encode(), h, timeout=timeout)


def post_form(url, fields, timeout=60):
    return http(url, urllib.parse.urlencode(fields).encode(), timeout=timeout)


def multipart(fields, files):
    """files: {field: (filename, bytes, mime)}"""
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fn, data, mime) in files.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'
                   f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n")
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


def retry(fn, attempts=3, wait=5, what="call"):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"{what} failed (try {i + 1}/{attempts}): {str(e)[:300]}")
            if i < attempts - 1:
                time.sleep(wait * (i + 1))
    raise last
