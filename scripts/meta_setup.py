"""One-time Meta setup helper (keys are never printed).
  python meta_setup.py check            -> verifies META_APP_ID / META_APP_SECRET
  python meta_setup.py page             -> reads META_USER_TOKEN (short-lived, from Graph API Explorer),
                                           exchanges it for a long-lived token, finds the Page and saves a
                                           never-expiring FB_PAGE_TOKEN + FB_PAGE_ID (+ IG_USER_ID if linked) to .env
"""
import json, os, sys, urllib.request, urllib.parse, urllib.error
sys.path.insert(0, os.path.dirname(__file__))
from cf_image import load_env, ROOT

GRAPH = "https://graph.facebook.com/v23.0"

def get(path, **params):
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = json.loads(e.read() or b"{}").get("error", {})
        raise SystemExit(f"Meta error {e.code}: {err.get('message')} (code {err.get('code')})")

def set_env(key, value):
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines()
    done = False
    for i, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[i] = f"{key}={value}"; done = True
    if not done:
        lines.append(f"{key}={value}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")

load_env()
app_id, secret = os.environ.get("META_APP_ID", ""), os.environ.get("META_APP_SECRET", "")
print("app id length:", len(app_id), "| secret length:", len(secret))
cmd = sys.argv[1] if len(sys.argv) > 1 else "check"

if cmd == "check":
    tok = get("oauth/access_token", client_id=app_id, client_secret=secret, grant_type="client_credentials")
    app = get(app_id, access_token=tok["access_token"], fields="name")
    print("app OK:", app.get("name"))

elif cmd == "page":
    short = os.environ.get("META_USER_TOKEN", "")
    if not short:
        raise SystemExit("MISSING META_USER_TOKEN")
    long_tok = get("oauth/access_token", grant_type="fb_exchange_token", client_id=app_id,
                   client_secret=secret, fb_exchange_token=short)["access_token"]
    perms = get("me/permissions", access_token=long_tok).get("data", [])
    print("granted:", sorted(p["permission"] for p in perms if p["status"] == "granted"))
    pages = get("me/accounts", access_token=long_tok,
                fields="id,name,access_token,instagram_business_account").get("data", [])
    print("pages:", [p["name"] for p in pages])
    page = next((p for p in pages if "Sky" in p["name"]), pages[0] if pages else None)
    if not page:
        raise SystemExit("NO PAGE FOUND - was the Page selected when granting access?")
    dbg = get("debug_token", input_token=page["access_token"], access_token=f"{app_id}|{secret}")["data"]
    print("page token expires_at:", dbg.get("expires_at"), "(0 = never)")
    set_env("FB_PAGE_ID", page["id"])
    set_env("FB_PAGE_TOKEN", page["access_token"])
    ig = page.get("instagram_business_account", {}).get("id")
    if ig:
        set_env("IG_USER_ID", ig)
    set_env("META_USER_TOKEN", "")   # short-lived token no longer needed
    print("saved FB_PAGE_ID, FB_PAGE_TOKEN" + (", IG_USER_ID" if ig else " (instagram not linked yet)"))
