"""Edit/generate an image with a Gemini image model (free tier if available).
CLI: python gemini_image.py MODEL OUT.jpg "prompt" [input images...]"""
import base64, json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(__file__))
from cf_image import load_env

def edit(model, out, prompt, images=(), aspect=None):
    load_env()
    key = os.environ["GEMINI_API_KEY"]
    parts = [{"text": prompt}]
    for p in images:
        mime = "image/png" if p.lower().endswith(".png") else "image/jpeg"
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(open(p, "rb").read()).decode()}})
    body = {"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
    if aspect:
        body["generationConfig"]["imageConfig"] = {"aspectRatio": aspect}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode(), headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{model}: HTTP {e.code}: {e.read()[:400]!r}") from None
    for part in data["candidates"][0]["content"]["parts"]:
        blob = part.get("inline_data") or part.get("inlineData")
        if blob:
            open(out, "wb").write(base64.b64decode(blob["data"]))
            return out
    raise RuntimeError(f"{model}: no image returned: {json.dumps(data)[:400]}")

if __name__ == "__main__":
    model, out, prompt, *imgs = sys.argv[1:]
    print(edit(model, out, prompt, imgs, aspect="16:9"))
