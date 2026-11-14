"""Checks the Gemini key in .env and lists usable Flash models (key is never printed)."""
import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(__file__))
from cf_image import load_env

load_env()
key = os.environ.get("GEMINI_API_KEY", "")
print("gemini key length:", len(key))
if not key:
    sys.exit("MISSING GEMINI_API_KEY")

def call(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

st, data = call("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200")
print("list status:", st)
names = [m["name"].split("/")[1] for m in data.get("models", [])
         if "generateContent" in m.get("supportedGenerationMethods", [])]
flash = [n for n in names if "flash" in n]
print("flash models:", flash)

for model in sys.argv[1:] or []:
    st, d = call(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                 {"contents": [{"parts": [{"text": "Say hi in 3 words."}]}]})
    txt = d.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text") if st == 200 else d.get("error", {}).get("message", "")[:300]
    print(f"{model}: {st} -> {txt!r}")
