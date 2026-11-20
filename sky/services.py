"""Clients for the outside services: Gemini, Cloudflare, Telegram, Meta (Facebook/Instagram/Threads)."""
import base64
import json
import time

from .util import (HttpError, env, http, log, multipart, post_form, post_json, retry)

# ======================= Gemini (text + vision) =======================
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.8-flash"]


def gemini_json(prompt, images=()):
    """Ask Gemini for a JSON answer. Falls back to the next model on quota/errors."""
    parts = [{"text": prompt}]
    for path in images:
        parts.append({"inline_data": {"mime_type": "image/jpeg",
                                      "data": base64.b64encode(open(path, "rb").read()).decode()}})
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}}
    last = None
    # Gemini is often overloaded (503): go through all models, up to 3 rounds with growing pauses
    for round_ in range(3):
      if round_:
        log(f"all Gemini models busy, waiting before round {round_ + 1}")
        time.sleep(60 * round_)
      for model in GEMINI_MODELS:
        try:
            data = post_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                             body, {"x-goog-api-key": env("GEMINI_API_KEY")}, timeout=100)
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            return json.loads(text)
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"gemini {model} failed: {str(e)[:150]}")
            time.sleep(3)
    raise RuntimeError(f"all Gemini models failed: {last}")


# ======================= Cloudflare Workers AI (images) =======================
CF_MODEL = "@cf/black-forest-labs/flux-2-klein-9b"


class QuotaExceeded(RuntimeError):
    pass


def cf_image(prompt, refs=(), width=832, height=1040, seed=None):
    fields = {"prompt": prompt, "width": str(width), "height": str(height)}
    if seed is not None:
        fields["seed"] = str(seed)
    files = {f"input_image_{i}": (f"ref{i}.jpg", open(p, "rb").read(), "image/jpeg") for i, p in enumerate(refs)}
    body, ctype = multipart(fields, files)
    url = f"https://api.cloudflare.com/client/v4/accounts/{env('CLOUDFLARE_ACCOUNT_ID')}/ai/run/{CF_MODEL}"

    def call():
        try:
            return http(url, body, {"Authorization": f"Bearer {env('CLOUDFLARE_API_TOKEN')}",
                                    "Content-Type": ctype}, timeout=240)
        except HttpError as e:
            if e.code == 429 or "daily free allocation" in e.body:
                raise QuotaExceeded("Cloudflare daily free image quota is used up") from None
            raise

    def call_retry_on_500():
        try:
            return call()
        except QuotaExceeded:
            raise
        except HttpError as e:
            if e.code >= 500:
                raise
            raise RuntimeError(str(e)) from None

    for i in range(4):
        try:
            data = call_retry_on_500()
            return base64.b64decode(data["result"]["image"])
        except QuotaExceeded:
            raise
        except HttpError as e:  # temporary server error
            log(f"cloudflare 5xx (try {i + 1}/4): {e.body[:150]}")
            time.sleep(6 * (i + 1))
    raise RuntimeError("Cloudflare kept failing with server errors")


# ======================= Telegram =======================
def _tg(method):
    return f"https://api.telegram.org/bot{env('TELEGRAM_BOT_TOKEN')}/{method}"


def tg_send(text):
    for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]:
        retry(lambda: post_form(_tg("sendMessage"), {"chat_id": env("TELEGRAM_CHAT_ID"), "text": chunk}),
              what="telegram send")


def tg_photo(path, caption=""):
    body, ctype = multipart({"chat_id": env("TELEGRAM_CHAT_ID"), "caption": caption[:1000]},
                            {"photo": ("post.jpg", open(path, "rb").read(), "image/jpeg")})
    retry(lambda: http(_tg("sendPhoto"), body, {"Content-Type": ctype}), what="telegram photo")


def tg_document(path, caption=""):
    """Send as a file (full quality, for re-uploading to TikTok)."""
    body, ctype = multipart({"chat_id": env("TELEGRAM_CHAT_ID"), "caption": caption[:1000]},
                            {"document": ("sky_post.jpg", open(path, "rb").read(), "image/jpeg")})
    retry(lambda: http(_tg("sendDocument"), body, {"Content-Type": ctype}), what="telegram document")


def tg_updates(offset):
    """New messages from the owner's chat since `offset`. Returns (texts, new_offset)."""
    data = retry(lambda: post_form(_tg("getUpdates"), {"offset": offset, "timeout": 0}), attempts=4, wait=10,
                 what="telegram getUpdates")
    texts, new_offset = [], offset
    for u in data.get("result", []):
        new_offset = max(new_offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != env("TELEGRAM_CHAT_ID"):
            continue
        if msg.get("photo"):  # owner sent a ready image -> it goes to the queue
            texts.append({"photo": msg["photo"][-1]["file_id"], "text": msg.get("caption", ""),
                          "date": msg.get("date", 0)})
        elif msg.get("document", {}).get("mime_type", "").startswith("image/"):
            texts.append({"photo": msg["document"]["file_id"], "text": msg.get("caption", ""),
                          "date": msg.get("date", 0)})
        elif msg.get("text"):
            texts.append({"text": msg["text"], "date": msg.get("date", 0)})
    return texts, new_offset


def tg_download(file_id, dest):
    info = post_form(_tg("getFile"), {"file_id": file_id})["result"]
    data = http(f"https://api.telegram.org/file/bot{env('TELEGRAM_BOT_TOKEN')}/{info['file_path']}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


# ======================= Meta =======================
GRAPH = "https://graph.facebook.com/v23.0"
THREADS = "https://graph.threads.net/v1.0"


def fb_post_photo(image_path, caption):
    """Publish a photo post on the Facebook Page (uploads the file directly)."""
    body, ctype = multipart({"caption": caption, "access_token": env("FB_PAGE_TOKEN")},
                            {"source": ("post.jpg", open(image_path, "rb").read(), "image/jpeg")})
    r = retry(lambda: http(f"{GRAPH}/{env('FB_PAGE_ID')}/photos", body, {"Content-Type": ctype}),
              what="facebook post")
    return r.get("post_id") or r.get("id")


def ig_post_photo(image_url, caption):
    """Instagram: create a media container from a public URL, wait until ready, publish."""
    ig, tok = env("IG_USER_ID"), env("FB_PAGE_TOKEN")
    c = retry(lambda: post_form(f"{GRAPH}/{ig}/media", {"image_url": image_url, "caption": caption,
                                                        "access_token": tok}), what="instagram container")
    cid = c["id"]
    for _ in range(20):
        st = http(f"{GRAPH}/{cid}?fields=status_code&access_token={tok}")
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"instagram rejected the image: {st}")
        time.sleep(5)
    r = retry(lambda: post_form(f"{GRAPH}/{ig}/media_publish", {"creation_id": cid, "access_token": tok}),
              what="instagram publish")
    return r.get("id")


def threads_post_photo(image_url, text):
    uid, tok = env("THREADS_USER_ID"), env("THREADS_TOKEN")
    c = retry(lambda: post_form(f"{THREADS}/{uid}/threads", {"media_type": "IMAGE", "image_url": image_url,
                                                             "text": text[:500], "access_token": tok}),
              what="threads container")
    time.sleep(30)  # Meta recommends waiting before publishing
    r = retry(lambda: post_form(f"{THREADS}/{uid}/threads_publish", {"creation_id": c["id"], "access_token": tok}),
              what="threads publish")
    return r.get("id")


def threads_refresh_token():
    """Long-lived Threads tokens last 60 days; refreshing returns a new one."""
    r = http(f"https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token"
             f"&access_token={env('THREADS_TOKEN')}")
    return r["access_token"], r.get("expires_in")
