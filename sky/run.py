"""Entry point.  python -m sky.run <command>

  post            make and publish one post (scheduled runs respect config + /pause)
  post --test     make one post and send it ONLY to Telegram as a preview (nothing is published)
  weekly-ask      Sunday morning: ask the owner on Telegram for this week's topics
  weekly-plan     Sunday evening: read the answer (or choose alone) and save the week plan
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

from . import content
from .services import (QuotaExceeded, cf_image, fb_post_photo, ig_post_photo, tg_document, tg_photo,
                       tg_send, tg_updates, threads_post_photo)
from .util import (PAGES_BASE, POSTS, RAW_BASE, REFS, ROOT, config, load_env, log, now, read_state,
                   write_state)

ON_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"


# ---------------- Telegram inbox (also handles /pause /resume /status) ----------------
def sync_inbox():
    tg = read_state("telegram.json", {"offset": 0, "paused": False})
    texts, tg["offset"] = tg_updates(tg.get("offset", 0))
    inbox = read_state("inbox.json", [])
    for m in texts:
        t = m["text"].strip()
        cmd = t.split()[0].lower() if t.startswith("/") else ""
        if cmd == "/pause":
            tg["paused"] = True
            tg_send("⏸️ עצרתי. לא אפרסם עד שתשלח /resume")
        elif cmd == "/resume":
            tg["paused"] = False
            tg_send("▶️ חזרתי לפרסם לפי הלוח.")
        elif cmd == "/status":
            h = read_state("history.json", [])
            last = h[-1]["date"] if h else "עדיין לא"
            tg_send(f"מצב: {'⏸️ עצור' if tg.get('paused') else '▶️ פעיל'}\nפוסט אחרון: {last}\nסה\"כ פוסטים: {len(h)}")
        elif cmd in ("/start", "/help"):
            tg_send("פקודות: /pause לעצירה, /resume להמשך, /status למצב.\n"
                    "כל הודעה אחרת = רעיון או עדכון שאשתמש בו בפוסט הבא (למשל: \"ברצלונה ניצחה 3-1\").")
        else:
            inbox.append({"text": t, "date": m["date"], "used": False})
    write_state("telegram.json", tg)
    write_state("inbox.json", inbox[-50:])
    return tg, inbox


# ---------------- publishing helpers ----------------
def git_push(message):
    if not ON_GITHUB:
        return
    for cmd in (["git", "add", "docs/posts", "state"],
                ["git", "commit", "-m", message],
                ["git", "pull", "--rebase", "--autostash"],
                ["git", "push"]):
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if r.returncode and cmd[1] != "commit":
            log("git:", " ".join(cmd), r.stderr[-300:])


def public_url(rel):
    """Wait until the image is reachable on GitHub Pages; fall back to the raw GitHub URL."""
    for base, tries in ((PAGES_BASE, 24), (RAW_BASE, 6)):
        url = f"{base}/{rel}"
        for _ in range(tries):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=20) as r:
                    if r.status == 200:
                        return url
            except Exception:  # noqa: BLE001
                pass
            time.sleep(10)
    raise RuntimeError("image never became publicly reachable")


def make_image(post, out_path, attempts):
    """Generate up to `attempts` images; keep the first one that passes the quality check."""
    for i in range(attempts):
        img = cf_image(post["image_prompt"], refs=REFS, seed=int(time.time()) % 100000 + i)
        out_path.write_bytes(img)
        ok, report = content.check_image(out_path)
        if ok:
            return True, report
    return False, report


# ---------------- commands ----------------
def cmd_post(test=False):
    cfg = config()
    tg, inbox = sync_inbox()
    scheduled = os.environ.get("SKY_SCHEDULED") == "1"
    if scheduled and (not cfg.get("schedule_enabled") or tg.get("paused")):
        log("schedule disabled or paused - nothing to do")
        git_push("sync inbox")
        return

    history = read_state("history.json", [])
    plan = read_state("week_plan.json", {})
    notes = plan.get(content.today_he(), "")
    last_ts = history[-1]["ts"] if history else 0
    fresh = [m for m in inbox if not m["used"] and m["date"] > last_ts - 3 * 86400]

    post = content.write_post(history, notes, [m["text"] for m in fresh])
    log("post idea:", post["pillar"], "|", post["scene"])

    stamp = now().strftime("%Y-%m-%d_%H%M")
    folder = (ROOT / "drafts" / "test") if test else POSTS
    folder.mkdir(parents=True, exist_ok=True)
    img_path = folder / f"{stamp}.jpg"
    try:
        ok, report = make_image(post, img_path, cfg.get("max_image_attempts", 3))
    except QuotaExceeded:
        tg_send("⚠️ המכסה היומית של Cloudflare לתמונות נגמרה, אז הפוסט הזה ידלג. ננסה שוב בריצה הבאה.")
        git_push("sync inbox")
        raise
    caption = content.caption_text(post)

    if test or not ok:
        head = "🧪 תצוגה מקדימה, לא פורסם" if test else "⚠️ התמונה לא עברה בדיקת איכות, אז לא פרסמתי. הנה מה שיצא:"
        problems = "" if ok else f"\nבעיות: {', '.join(report.get('problems', []))}"
        tg_photo(img_path, f"{head}{problems}\n\n{caption}")
        if not ok and not test:
            img_path.unlink(missing_ok=True)
        git_push("sync inbox")
        return

    (folder / f"{stamp}.json").write_text(json.dumps({**post, "caption": caption}, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    results, platforms = {}, cfg.get("platforms", {})
    if platforms.get("facebook"):
        try:
            results["facebook"] = fb_post_photo(img_path, caption)
        except Exception as e:  # noqa: BLE001
            results["facebook"] = f"ERROR {str(e)[:200]}"
    if platforms.get("instagram") or platforms.get("threads"):
        git_push(f"post {stamp}")
        url = public_url(f"posts/{img_path.name}")
        if platforms.get("instagram"):
            try:
                results["instagram"] = ig_post_photo(url, caption)
            except Exception as e:  # noqa: BLE001
                results["instagram"] = f"ERROR {str(e)[:200]}"
        if platforms.get("threads"):
            try:
                results["threads"] = threads_post_photo(url, caption)
            except Exception as e:  # noqa: BLE001
                results["threads"] = f"ERROR {str(e)[:200]}"

    for m in fresh:
        m["used"] = True
    write_state("inbox.json", inbox)
    history.append({"date": stamp, "ts": int(time.time()), "pillar": post["pillar"], "scene": post["scene"],
                    "results": results})
    write_state("history.json", history[-200:])

    status = "\n".join(f"{'✅' if not str(v).startswith('ERROR') else '❌'} {k}: "
                       f"{'פורסם' if not str(v).startswith('ERROR') else v}" for k, v in results.items())
    tg_photo(img_path, f"📢 פוסט חדש עלה\n{status}\n\n{caption}")
    tg_document(img_path, "🎵 לטיקטוק: הקובץ באיכות מלאה. הכיתוב מוכן בהודעה הקודמת, רק להעתיק.")
    git_push(f"post {stamp}")


def schedule_off():
    return os.environ.get("SKY_SCHEDULED") == "1" and not config().get("schedule_enabled")


def cmd_weekly_ask():
    if schedule_off():
        return
    sync_inbox()
    st = read_state("weekly.json", {})
    st["asked_ts"] = int(time.time())
    write_state("weekly.json", st)
    tg_send("☀️ בוקר טוב! מה הנושאים של Sky השבוע?\n"
            "אפשר לכתוב חופשי, למשל: \"ראשון ורביעי ים, שני קפה בתל אביב, חמישי ברסה\".\n"
            "אם לא תענה עד הערב, אבחר לבד 🙂")
    git_push("weekly ask")


def cmd_weekly_plan():
    if schedule_off():
        return
    tg, inbox = sync_inbox()
    asked = read_state("weekly.json", {}).get("asked_ts", 0)
    answers = [m for m in inbox if not m["used"] and m["date"] >= asked]
    if answers:
        plan = content.plan_week(" | ".join(m["text"] for m in answers))
        for m in answers:
            m["used"] = True
        write_state("inbox.json", inbox)
        tg_send(f"👍 קיבלתי. {plan.get('summary_he', '')}")
    else:
        plan = {}
        tg_send("לא קיבלתי תשובה, אז השבוע אבחר את הנושאים בעצמי לפי האישיות של Sky 🙂")
    plan["week_of"] = now().strftime("%Y-%m-%d")
    write_state("week_plan.json", plan)
    git_push("weekly plan")


def main():
    load_env()
    args = sys.argv[1:] or ["post"]
    cmd = args[0]
    try:
        if cmd == "post":
            cmd_post(test="--test" in args)
        elif cmd == "weekly-ask":
            cmd_weekly_ask()
        elif cmd == "weekly-plan":
            cmd_weekly_plan()
        else:
            sys.exit(__doc__)
    except QuotaExceeded:
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        log("FAILED:", e)
        try:
            tg_send(f"❌ תקלה במערכת של Sky ({cmd}):\n{str(e)[:500]}")
        except Exception:  # noqa: BLE001
            pass
        raise


if __name__ == "__main__":
    main()
