"""Finds your Telegram chat id from the bot's updates, saves it to .env and sends a test message."""
import json, os, sys, urllib.request, urllib.parse
from pathlib import Path
sys.path.insert(0, os.path.dirname(__file__))
from cf_image import load_env, ROOT

load_env()
tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
print("token length:", len(tok))
api = f"https://api.telegram.org/bot{tok}"

def call(method, **params):
    data = urllib.parse.urlencode(params).encode() if params else None
    with urllib.request.urlopen(f"{api}/{method}", data=data, timeout=30) as r:
        return json.loads(r.read())

me = call("getMe")
print("bot ok:", me.get("ok"), "username:", me["result"].get("username"))

chat_id = os.environ.get("TELEGRAM_CHAT_ID")
if not chat_id:
    ups = call("getUpdates").get("result", [])
    chats = {u["message"]["chat"]["id"] for u in ups if "message" in u}
    print("chats found:", len(chats))
    if not chats:
        sys.exit("NO MESSAGES YET - send /start to the bot")
    chat_id = str(sorted(chats)[0])
    with open(ROOT / ".env", "a", encoding="utf-8") as f:
        f.write(f"\n# Your private chat with the bot\nTELEGRAM_CHAT_ID={chat_id}\n")
    print("saved chat id to .env")

res = call("sendMessage", chat_id=chat_id,
           text="היי, זאת Sky 👋\nהבוט מחובר ועובד. מכאן אשלח לך עותק של כל פוסט, ובימי ראשון אשאל מה הנושאים לשבוע.\n\n🤖 AI character")
print("message sent:", res.get("ok"))
