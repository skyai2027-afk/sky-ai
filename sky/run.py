"""Entry point.  python -m sky.run <command>

  post            prepare the next post (scheduled runs respect config + /pause)
  post --test     prepare one post and send it ONLY to Telegram as a preview (nothing is published)
  check           every ~30 min: read Telegram replies (choose / approve / edit), commands, new photos
  weekly-ask      Sunday morning: ask the owner on Telegram for this week's topics
  weekly-plan     Sunday evening: read the answer (or choose alone) and save the week plan
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from . import content
from .services import (QuotaExceeded, cf_image, fb_post_photo, ig_post_photo, tg_document, tg_download,
                       tg_photo, tg_send, tg_updates, threads_post_photo)
from .util import (PAGES_BASE, POSTS, RAW_BASE, REFS, ROOT, config, load_env, log, now, read_state,
                   write_state)

ON_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"

APPROVE = {"/ok", "ok", "אוקיי", "פרסם", "תפרסם", "לפרסם", "כן", "מאשר", "אשר", "יאללה", "👍", "✅"}
REJECT = {"/no", "no", "לא", "בטל", "דלג", "❌", "👎"}
NEW_IMAGE = {"/new", "תמונה חדשה", "/newimage"}
EDIT_PREFIX = ("תקן", "שנה", "edit", "/edit")
CHOICE = re.compile(r"^(?:פרסם|תפרסם|לפרסם|/ok|ok|אפשרות|תמונה)?\s*([1-9])$")

HELP = ("פקודות: /pause לעצירה, /resume להמשך, /status למצב, /clear לניקוי תור התמונות שלך.\n"
        "📸 שולחים תמונה: מקבלים ביקורת, ובפוסט הבא היא מוצעת לצד תמונה שאני מייצר. אתה בוחר.\n"
        "💡 כל הודעה אחרת: רעיון או עדכון לפוסט הבא (למשל: \"ברצלונה ניצחה 3-1\").")


# ---------------- Telegram inbox ----------------
def sync_inbox():
    tg = read_state("telegram.json", {"offset": 0, "paused": False})
    texts, tg["offset"] = tg_updates(tg.get("offset", 0))
    inbox = read_state("inbox.json", [])
    for m in texts:
        t = m["text"].strip()
        low = t.lower()
        cmd = low.split()[0] if low.startswith("/") else ""
        if cmd == "/pause":
            tg["paused"] = True
            tg_send("⏸️ עצרתי. לא אפרסם עד שתשלח /resume")
        elif cmd == "/resume":
            tg["paused"] = False
            tg_send("▶️ חזרתי לפרסם לפי הלוח.")
        elif cmd == "/status":
            h = read_state("history.json", [])
            last = h[-1]["date"] if h else "עדיין לא"
            tg_send(f"מצב: {'⏸️ עצור' if tg.get('paused') else '▶️ פעיל'}\nפוסט אחרון: {last}\n"
                    f"סה\"כ פוסטים: {len(h)}\nתמונות שלך בתור: {len(read_state('queue.json', []))}\n"
                    f"שלבים שנשארו בסיפור: {len(read_state('story.json', []))}")
        elif cmd in ("/start", "/help"):
            tg_send(HELP)
        elif cmd == "/clear":
            for q in read_state("queue.json", []):
                (ROOT / q["file"]).unlink(missing_ok=True)
            write_state("queue.json", [])
            tg_send("🗑️ התור של התמונות שלך התרוקן.")
        elif m.get("photo"):
            receive_photo(m, t)
        elif (low in APPROVE | REJECT | NEW_IMAGE or CHOICE.match(low)) and not read_state("pending.json", None):
            tg_send("אין כרגע פוסט שמחכה לאישור 🙂")
        else:
            inbox.append({"text": t, "date": m["date"], "used": False})
    write_state("telegram.json", tg)
    write_state("inbox.json", inbox[-50:])
    return tg, inbox


def receive_photo(m, note):
    """Owner sent a photo: save it in the queue and send an honest critique."""
    dest = ROOT / "state" / "queue" / f"{m['date']}.jpg"
    tg_download(m["photo"], dest)
    story = read_state("story.json", [])
    ctx = (story[0]["note"] if story else "") + (f" | owner note: {note}" if note else "")
    try:
        rev = content.review_photo(dest, ctx)
        review = f"{rev.get('text_he', '')}\n(ציון: {rev.get('score', '?')}/10)"
    except Exception as e:  # noqa: BLE001
        log("review failed:", e)
        review = None
    queue = read_state("queue.json", [])
    queue.append({"file": f"state/queue/{dest.name}", "note": note, "date": m["date"], "review": review})
    write_state("queue.json", queue)
    tg_send(f"📸 קיבלתי את התמונה (מספר {len(queue)} בתור).\n\n🧐 הביקורת שלי:\n"
            f"{review or 'לא הצלחתי לבדוק אותה כרגע.'}\n\n"
            "בפוסט הבא תקבל אותה ליד תמונה שאני מייצר, ותבחר.")


# ---------------- helpers ----------------
def git_push(message):
    if not ON_GITHUB:
        return
    for cmd in (["git", "add", "-A", "docs/posts", "state"],
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
    report = {}
    for i in range(attempts):
        img = cf_image(post["image_prompt"], refs=REFS, seed=int(time.time()) % 100000 + i)
        out_path.write_bytes(img)
        ok, report = content.check_image(out_path)
        if ok:
            return True, report
    return False, report


# ---------------- making a post ----------------
def cmd_post(test=False, image=None, note=""):
    """Prepare the next post. If the owner supplied a photo, BOTH it and a generated image are offered."""
    cfg = config()
    tg, inbox = sync_inbox()
    scheduled = os.environ.get("SKY_SCHEDULED") == "1"
    if scheduled and (not cfg.get("schedule_enabled") or tg.get("paused")):
        log("schedule disabled or paused - nothing to do")
        git_push("sync inbox")
        return

    history = read_state("history.json", [])
    notes = read_state("week_plan.json", {}).get(content.today_he(), "")
    last_ts = history[-1]["ts"] if history else 0
    fresh = [m for m in inbox if not m["used"] and m["date"] > last_ts - 3 * 86400]
    stamp = now().strftime("%Y-%m-%d_%H%M")
    work = ROOT / ("drafts/test" if test else "state/pending")
    work.mkdir(parents=True, exist_ok=True)

    # storyline: an ordered list of upcoming posts (e.g. a trip), used before the normal topic mix
    story = read_state("story.json", [])
    if story:
        notes = f"STORYLINE STEP (follow it exactly): {story[0]['note']}"

    # the owner's photo for this post, if any: command line > story step photo > photo sent on Telegram
    queue = read_state("queue.json", [])
    owner = None
    if image:
        owner = {"src": image, "note": note, "queue_file": None, "review": None}
    elif story and story[0].get("image"):
        owner = {"src": str(ROOT / story[0]["image"]), "note": story[0]["note"], "queue_file": None,
                 "review": None}
    elif queue:
        q = queue[0]
        owner = {"src": str(ROOT / q["file"]), "note": q.get("note", ""), "queue_file": q["file"],
                 "review": q.get("review")}

    options = []
    if owner:
        path = work / f"{stamp}_owner.jpg"
        path.write_bytes(open(owner["src"], "rb").read())
        post = content.caption_for_image(path, history, owner["note"] or notes, [])
        options.append({"source": "owner", "image": path.relative_to(ROOT).as_posix(), "post": post,
                        "caption": content.caption_text(post), "queue_file": owner["queue_file"],
                        "review": owner["review"]})

    extra_note = ""
    try:
        post = content.write_post(history, notes, [m["text"] for m in fresh])
        log("post idea:", post["pillar"], "|", post["scene"])
        path = work / f"{stamp}_gen.jpg"
        ok, report = make_image(post, path, cfg.get("max_image_attempts", 3))
        options.append({"source": "generated", "image": path.relative_to(ROOT).as_posix(), "post": post,
                        "caption": content.caption_text(post), "qc_ok": ok,
                        "qc_note": "" if ok else "⚠️ לא עברה את בדיקת האיכות במלואה: "
                                                 + ", ".join(report.get("problems", []))})
    except QuotaExceeded:
        if not owner:
            tg_send("⚠️ המכסה היומית של Cloudflare לתמונות נגמרה, אז הפוסט הזה ידלג. ננסה שוב בריצה הבאה.")
            git_push("sync inbox")
            raise
        extra_note = "(לא נשארה מכסה לייצר תמונה חלופית, אז יש רק את התמונה שלך)"

    if test:
        for i, o in enumerate(options, 1):
            tg_photo(ROOT / o["image"], f"🧪 בדיקה, אפשרות {i}\n\n{o['caption']}")
        return

    for m in fresh:
        m["used"] = True
    write_state("inbox.json", inbox)
    for o in options:  # the story moves on only when one of these is really published
        o["story_note"] = story[0]["note"] if story else None

    if not cfg.get("approval_mode"):
        best = (next((o for o in options if o["source"] == "owner"), None)
                or next((o for o in options if o.get("qc_ok")), None))
        if not best:
            cleanup({"options": options})
            tg_send("⚠️ התמונה לא עברה בדיקת איכות, אז לא פרסמתי.")
            git_push("sync inbox")
            return
        finish(best, stamp)
        cleanup({"options": options})
        git_push(f"post {stamp}")
        return

    old = read_state("pending.json", None)
    if old:
        cleanup(old)
        tg_send("ℹ️ הפוסט הקודם שחיכה לאישור הוחלף בפוסט החדש.")
    pending = {"stamp": stamp, "created_ts": int(time.time()), "options": options, "extra_note": extra_note}
    write_state("pending.json", pending)
    send_preview(pending)
    git_push(f"pending {stamp}")


def send_preview(pending):
    hours = config().get("approval_hours", 12)
    opts = pending["options"]
    for i, o in enumerate(opts, 1):
        who = "📸 התמונה שלך" if o["source"] == "owner" else "🎨 התמונה שאני יצרתי"
        extra = ""
        if o.get("review"):
            extra = f"\n\n🧐 הביקורת שלי:\n{o['review']}"
        elif o.get("qc_note"):
            extra = f"\n\n{o['qc_note']}"
        label = f"אפשרות {i}: {who}" if len(opts) > 1 else who
        tg_photo(ROOT / o["image"], f"{label}\n\n{o['caption']}{extra}")
    pick = ("✅ \"1\" או \"2\": מפרסמים את האפשרות שבחרת\n" if len(opts) > 1
            else "✅ \"פרסם\" (או /ok): מפרסמים\n")
    tg_send("🕐 פוסט מחכה לאישור שלך." + (f"\n{pending['extra_note']}" if pending.get("extra_note") else "")
            + "\n\nמה עושים?\n" + pick
            + "❌ \"דלג\": לא מפרסמים\n"
              "✏️ \"תקן: ...\": לשנות את הכיתוב, למשל \"תקן: בלי אימוג'י, ויותר מצחיק\"\n"
              "🔄 \"תמונה חדשה\": לייצר מחדש את התמונה שלי\n"
            + f"אם לא תענה תוך {hours} שעות, הפוסט לא יעלה.\n"
              "(אני בודק תשובות כל כחצי שעה, אז זה יכול לקחת כמה דקות.)")


def cleanup(pending):
    for o in pending.get("options", []):
        (ROOT / o["image"]).unlink(missing_ok=True)


def finish(option, stamp):
    """Publish the chosen option; an owner photo from the queue leaves the queue."""
    ok = publish(option["post"], ROOT / option["image"], option["caption"], stamp)
    if not ok:
        return False
    story = read_state("story.json", [])
    if option.get("story_note") and story and story[0]["note"] == option["story_note"]:
        write_state("story.json", story[1:])
        if len(story) == 1:
            tg_send("📖 הסיפור הסתיים. מהפוסט הבא חוזרים לתמהיל הרגיל של הנושאים.")
    if option.get("queue_file"):
        (ROOT / option["queue_file"]).unlink(missing_ok=True)
        write_state("queue.json", [q for q in read_state("queue.json", []) if q["file"] != option["queue_file"]])


def publish(post, img_path, caption, stamp):
    cfg = config()
    POSTS.mkdir(parents=True, exist_ok=True)
    dest = POSTS / f"{stamp}.jpg"
    dest.write_bytes(img_path.read_bytes())
    img_path = dest
    history = read_state("history.json", [])
    (POSTS / f"{stamp}.json").write_text(json.dumps({**post, "caption": caption}, ensure_ascii=False, indent=1),
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

    history.append({"date": stamp, "ts": int(time.time()), "pillar": post.get("pillar"),
                    "scene": post.get("scene"), "results": results})
    write_state("history.json", history[-200:])

    status = "\n".join(f"{'✅' if not str(v).startswith('ERROR') else '❌'} {k}: "
                       f"{'פורסם' if not str(v).startswith('ERROR') else v}" for k, v in results.items())
    tg_photo(img_path, f"📢 פוסט חדש עלה\n{status}\n\n{caption}")
    tg_document(img_path, "🎵 לטיקטוק: הקובץ באיכות מלאה. הכיתוב מוכן בהודעה הקודמת, רק להעתיק.")


# ---------------- answers on Telegram ----------------
def cmd_check():
    """Runs every ~30 min: handles choices / approvals / edits for the pending post, plus inbox commands."""
    cfg = config()
    sync_inbox()
    pending = read_state("pending.json", None)
    if not pending:
        git_push("sync inbox")
        return
    inbox = read_state("inbox.json", [])
    opts = pending["options"]
    replies = [m for m in inbox if not m["used"] and m["date"] >= pending["created_ts"]]
    for m in replies:
        t = m["text"].strip()
        low = t.lower()
        choice = CHOICE.match(low)
        idx = None
        if choice:
            idx = int(choice.group(1)) - 1
        elif low in APPROVE:
            if len(opts) == 1:
                idx = 0
            else:
                m["used"] = True
                tg_send("יש שתי אפשרויות 🙂 כתוב \"1\" או \"2\".")
                continue
        if idx is not None:
            m["used"] = True
            write_state("inbox.json", inbox)
            if idx >= len(opts):
                tg_send(f"אין אפשרות {idx + 1}. יש {len(opts)}.")
                continue
            write_state("pending.json", None)
            finish(opts[idx], pending["stamp"])
            cleanup(pending)
            git_push(f"post {pending['stamp']}")
            return
        if low in REJECT:
            m["used"] = True
            write_state("inbox.json", inbox)
            cleanup(pending)
            write_state("pending.json", None)
            tg_send("👌 דילגתי. הפוסט לא יעלה. (תמונה ששלחת נשארת בתור. /clear מנקה את התור.)")
            git_push("pending rejected")
            return
        if low in NEW_IMAGE:
            m["used"] = True
            write_state("inbox.json", inbox)
            gen = next((o for o in opts if o["source"] == "generated"), None)
            if not gen:
                tg_send("אין כאן תמונה שאני יצרתי, אז אין מה לייצר מחדש. אפשר לבחור, \"דלג\" או \"תקן: ...\".")
                continue
            ok, report = make_image(gen["post"], ROOT / gen["image"], cfg.get("max_image_attempts", 3))
            gen["qc_ok"] = ok
            gen["qc_note"] = "" if ok else "⚠️ גם התמונה הזו לא מושלמת: " + ", ".join(report.get("problems", []))
            pending["created_ts"] = int(time.time())
            write_state("pending.json", pending)
            send_preview(pending)
            git_push("pending new image")
            return
        if low.startswith(EDIT_PREFIX):
            m["used"] = True
            write_state("inbox.json", inbox)
            fix = t.split(":", 1)[1].strip() if ":" in t else t
            for o in opts:
                o["post"] = content.revise_caption(o["post"], fix)
                o["caption"] = content.caption_text(o["post"])
            pending["created_ts"] = int(time.time())
            write_state("pending.json", pending)
            send_preview(pending)
            git_push("pending caption edited")
            return
        # anything else stays in the inbox as an idea for a future post
    write_state("inbox.json", inbox)
    if time.time() - pending["created_ts"] > cfg.get("approval_hours", 12) * 3600:
        cleanup(pending)
        write_state("pending.json", None)
        tg_send("⌛ לא קיבלתי אישור בזמן, אז הפוסט האחרון לא עלה. ננסה שוב בפוסט הבא.")
    git_push("sync inbox")


# ---------------- weekly topics ----------------
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
            image = args[args.index("--image") + 1] if "--image" in args else None
            note = args[args.index("--note") + 1] if "--note" in args else ""
            cmd_post(test="--test" in args, image=image, note=note)
        elif cmd == "check":
            cmd_check()
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
