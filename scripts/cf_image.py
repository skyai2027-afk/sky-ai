"""Generate an image with Cloudflare Workers AI (FLUX.2 klein).

Usage as a library: generate(prompt, out_path, refs=[...], width=, height=, seed=)
Usage from CLI:     python cf_image.py out.jpg "prompt" [ref1.jpg ref2.jpg ...]
"""
import base64
import json
import os
import sys
import uuid
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = os.environ.get("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-2-klein-9b")


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _multipart(fields, files):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, path in files.items():
        data = Path(path).read_bytes()
        mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{Path(path).name}"\r\nContent-Type: {mime}\r\n\r\n'.encode() + data + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def generate(prompt, out_path, refs=(), width=768, height=1024, seed=None):
    load_env()
    acc = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    tok = os.environ["CLOUDFLARE_API_TOKEN"]
    fields = {"prompt": prompt, "width": str(width), "height": str(height)}
    if seed is not None:
        fields["seed"] = str(seed)
    files = {f"input_image_{i}": p for i, p in enumerate(refs)}
    body, ctype = _multipart(fields, files)
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{MODEL}",
        data=body,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": ctype},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Cloudflare error {e.code}: {e.read()[:800]!r}") from None
    if not data.get("success", True) or "result" not in data:
        raise RuntimeError(f"Cloudflare error: {json.dumps(data)[:800]}")
    img = base64.b64decode(data["result"]["image"])
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(img)
    return out_path


if __name__ == "__main__":
    out, prompt, *refs = sys.argv[1:]
    print(generate(prompt, out, refs))
