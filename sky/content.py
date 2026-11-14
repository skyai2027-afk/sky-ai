"""Decides what Sky posts, writes the caption + image prompt, and checks image quality."""
import random

from .services import gemini_json
from .util import REFS, ROOT, log, now

PILLARS = {  # target share of posts, from persona.md
    "sports": 25, "food": 25, "travel": 20, "fashion": 15, "fitness": 15,
}
DAYS_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

APPEARANCE = (
    "Sky, the same young woman as in the reference images: 24 years old, shoulder-length light honey-brown wavy hair "
    "(uniform color), light green eyes, straight dark natural eyebrows, soft rounded face, warm wide smile, many light "
    "freckles across nose, cheeks and forehead, warm Mediterranean skin, minimal makeup, small silver hoop earrings, "
    "thin delicate gold chain necklace"
)
IMAGE_RULES = (
    "Photorealistic candid photo that looks like a real phone or film photo, natural light, realistic skin texture, "
    "her face clearly visible and in focus (medium shot or closer). Clothing has no logos, no brand marks and no "
    "readable text. No real famous people. No official team crests."
)


def persona():
    return (ROOT / "persona.md").read_text(encoding="utf-8")


def pick_pillar(history):
    """Pick the topic that is most 'behind' its target share over the last 20 posts."""
    recent = [h.get("pillar") for h in history[-20:]]
    total = max(len(recent), 1)
    deficit = {p: share / 100 - recent.count(p) / total for p, share in PILLARS.items()}
    if recent:
        deficit[recent[-1]] -= 0.5  # avoid the same topic twice in a row
    best = sorted(deficit.items(), key=lambda kv: kv[1], reverse=True)[:2]
    return random.choice([p for p, _ in best])


def write_post(history, notes_today="", owner_messages=()):
    """Returns dict: pillar, scene, image_prompt, caption_en, caption_he, hashtags."""
    pillar = pick_pillar(history)
    recent = "\n".join(f"- {h['date']}: [{h.get('pillar')}] {h.get('scene', '')}" for h in history[-14:]) or "(none yet)"
    msgs = "\n".join(f"- {m}" for m in owner_messages) or "(none)"
    today = now()
    prompt = f"""You are the content brain of an AI-generated virtual influencer. Here is her full persona bible:

<persona>
{persona()}
</persona>

Today is {today:%A %d %B %Y}, {today:%H:%M} Israel time (season matters: clothes and light must fit it).
Suggested topic for this post: "{pillar}" (one of: sports, food, travel, fashion, fitness).
Owner's plan for today (overrides the suggested topic if it says something): {notes_today or "(nothing specific)"}
Recent messages from the owner (news, ideas; use them if relevant, e.g. a real match result he reported): {msgs}

Recent posts — do NOT repeat these scenes, places or jokes:
{recent}

Rules:
- Never invent real match scores, results or news. Only use facts the owner gave above.
- No real people in the picture, no logos, no team crests. Fans wear plain blue-red striped shirts without crests.
- Captions are short, witty, slightly cynical, warm underneath (see persona tone). 1-3 sentences each.
- English caption first; the Hebrew caption is natural Hebrew, not a word-for-word translation.
- Max 1-2 emojis per language.
- 5-8 hashtags, always include #AIgenerated and #virtualinfluencer, plus topic tags and 1-2 local tags. No #love/#instagood.
- The scene must be realistic for a 24 year old living in Tel Aviv (or a trip, if travel).

Return JSON with exactly these keys:
{{"pillar": "...", "scene": "one short line in English describing place + activity",
 "outfit": "what she wears (fits the persona style and the season)",
 "image_scene_prompt": "2-4 sentences in English describing ONLY the setting, action, pose, outfit, lighting and camera angle (do not describe her face; that is added automatically)",
 "caption_en": "...", "caption_he": "...", "hashtags": ["#..", "..."]}}"""
    post = gemini_json(prompt)
    post["pillar"] = post.get("pillar") or pillar
    tags = [t if t.startswith("#") else "#" + t for t in post.get("hashtags", [])]
    for must in ("#AIgenerated", "#virtualinfluencer"):
        if must.lower() not in [t.lower() for t in tags]:
            tags.append(must)
    post["hashtags"] = tags[:9]
    post["image_prompt"] = f"{APPEARANCE}. {post['image_scene_prompt']} Outfit: {post.get('outfit', '')}. {IMAGE_RULES}"
    return post


def caption_text(post):
    return f"{post['caption_en']}\n\n{post['caption_he']}\n\n{' '.join(post['hashtags'])}"


def check_image(image_path):
    """Gemini compares the new image with Sky's reference photos. Returns (ok, report)."""
    prompt = """The first two images are reference photos of a fictional character called Sky.
The LAST image is a newly generated photo that is supposed to show the same person.
Judge it strictly, like a photo editor before publishing to Instagram. Return JSON:
{"same_person": true/false (same face: freckles, face shape, eyes, hair color),
 "face_visible": true/false,
 "anatomy_ok": true/false (hands, fingers, limbs, eyes look natural),
 "logos_or_text": true/false (visible brand logos or garbled text),
 "looks_real": 1-10,
 "problems": ["short list"],
 "verdict": "pass" or "fail"}
Verdict is "pass" only if same_person, face_visible and anatomy_ok are true, logos_or_text is false and looks_real >= 7."""
    rep = gemini_json(prompt, images=[*REFS, image_path])
    ok = (rep.get("verdict") == "pass" and rep.get("same_person") and rep.get("anatomy_ok")
          and not rep.get("logos_or_text"))
    log("quality check:", "PASS" if ok else "FAIL", rep.get("problems"))
    return bool(ok), rep


def plan_week(owner_text):
    """Turn the owner's free-text weekly answer into per-day notes (keys: Hebrew day names)."""
    prompt = f"""The owner of a virtual influencer account answered the weekly question "what are the topics this week?".
His answer (Hebrew, free text): "{owner_text}"
Persona summary: Sky Fiori, 24, Tel Aviv, half Spanish half Italian, Barça fan, tennis, food, sea, travel.

Map his answer to days of the week. Hebrew day names: ראשון שני שלישי רביעי חמישי שישי שבת.
If he gives a general theme without days, spread it sensibly. Days he did not mention get "".
Return JSON: {{"ראשון": "...", "שני": "...", "שלישי": "...", "רביעי": "...", "חמישי": "...", "שישי": "...", "שבת": "...",
 "summary_he": "one short Hebrew sentence confirming the plan"}}
Write each day's note in English (it is passed to the content writer)."""
    return gemini_json(prompt)


def today_he():
    return DAYS_HE[now().weekday()]
