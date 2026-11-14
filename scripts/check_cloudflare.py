"""Checks that the Cloudflare keys in .env work, without printing them."""
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    load_env()
    acc = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    tok = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    print(f"account id length: {len(acc)} (expected 32)")
    print(f"token length: {len(tok)}")
    if not acc or not tok:
        print("MISSING VALUES")
        sys.exit(1)

    base = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai"
    status, data = get(f"{base}/models/search?search=flux", tok)
    print("models/search status:", status, "success:", data.get("success"))
    if not data.get("success"):
        print("errors:", data.get("errors"))
        sys.exit(1)
    names = sorted(m.get("name") for m in data.get("result", []))
    print("flux models:", names)

    model = sys.argv[1] if len(sys.argv) > 1 else "@cf/black-forest-labs/flux-2-klein-9b"
    status, data = get(f"{base}/models/schema?model={model}", tok)
    print(f"\nschema for {model} (status {status}):")
    print(json.dumps(data.get("result", data), indent=1)[:4000])


if __name__ == "__main__":
    main()
