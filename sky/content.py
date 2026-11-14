"""Decides what Sky posts, writes the caption + image prompt, and checks image quality."""
import json
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
- Do NOT imply that a real match is happening today/tonight unless the owner said so. Without owner info, sports
  posts are about HER doing sport (tennis, beach volleyball, shooting hoops, running) or general fan life
  (wearing the shirt on a normal day, a jersey on the chair), never "watching the game now".
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


def caption_for_image(image_path, history, notes="", owner_messages=()):
    """For a photo the owner made himself: look at it and write the caption."""
    recent = "\n".join(f"- {h['date']}: {h.get('scene', '')}" for h in history[-10:]) or "(none yet)"
    msgs = "\n".join(f"- {m}" for m in owner_messages) or "(none)"
    prompt = f"""You write Instagram captions for an AI-generated virtual influencer. Her persona bible:
<persona>
{persona()}
</persona>
The attached photo will be posted as-is. Look at it carefully and write the caption for THIS photo.
Owner's note about it: {notes or "(none)"}
Recent owner messages: {msgs}
Recent posts (do not repeat their jokes): {recent}
Rules: short, witty, slightly cynical, warm. English first, then natural Hebrew (not a literal translation).
Max 1-2 emojis per language. Never invent facts (scores, news). Do not mention real businesses by name
even if a sign is visible. 5-8 hashtags incl. #AIgenerated and #virtualinfluencer, no #love/#instagood.
Return JSON: {{"pillar": "sports|food|travel|fashion|fitness", "scene": "one short English line describing the photo",
 "caption_en": "...", "caption_he": "...", "hashtags": ["#.."]}}"""
    post = gemini_json(prompt, images=[image_path])
    tags = [t if t.startswith("#") else "#" + t for t in post.get("hashtags", [])]
    for must in ("#AIgenerated", "#virtualinfluencer"):
        if must.lower() not in [t.lower() for t in tags]:
            tags.append(must)
    post["hashtags"] = tags[:9]
    return post


def revise_caption(post, feedback):
    """Rewrite the caption following the owner's feedback (Hebrew or English)."""
    prompt = f"""Rewrite this Instagram caption of the virtual influencer Sky Fiori following the owner's feedback.
Persona: witty, slightly cynical, warm, 24, Tel Aviv. English caption first, natural Hebrew second.
Current: {json.dumps({k: post.get(k) for k in ("caption_en", "caption_he", "hashtags")}, ensure_ascii=False)}
Owner's feedback: "{feedback}"
Keep #AIgenerated and #virtualinfluencer. Return JSON: {{"caption_en": "...", "caption_he": "...", "hashtags": ["#.."]}}"""
    new = gemini_json(prompt)
    out = {**post, **{k: new[k] for k in ("caption_en", "caption_he", "hashtags") if k in new}}
    for must in ("#AIgenerated", "#virtualinfluencer"):
        if must.lower() not in [t.lower() for t in out["hashtags"]]:
            out["hashtags"].append(must)
    return out


def caption_text(post):
    return f"{post['caption_en']}\n\n{post['caption_he']}\n\n{' '.join(post['hashtags'])}"


def check_image(image_path):
    """Gemini compares the new image with Sky's reference photos. Returns (ok, report)."""
    prompt = """All images except the LAST one are reference photos of a fictional character called Sky.
Her identity markers: MANY visible freckles across nose and cheeks, soft ROUNDED face, light green eyes,
golden honey-brown shoulder-length wavy hair (may look lighter in sun or tied in a bun; not red, not dark), warm wide smile.
The LAST image is a newly generated photo that is supposed to show the same person.
You are a strict photo editor. A follower who knows Sky must instantly recognise her.
Compare feature by feature. If the face is too small or blurry to verify the freckles and face shape, that is a FAIL.
Return JSON:
{"freckles_match": true/false, "face_shape_match": true/false, "hair_color_match": true/false,
 "identity_score": 1-10 (10 = unmistakably the same person),
 "face_visible": true/false (large and sharp enough to verify),
 "anatomy_ok": true/false (hands, fingers, limbs, eyes look natural),
 "logos_or_text": true/false (visible brand logos or garbled text),
 "looks_real": 1-10,
 "problems": ["short list"]}"""
    rep = gemini_json(prompt, images=[*REFS, image_path])
    ok = (rep.get("freckles_match") and rep.get("face_shape_match") and rep.get("hair_color_match")
          and (rep.get("identity_score") or 0) >= 7 and rep.get("face_visible") and rep.get("anatomy_ok")
          and not rep.get("logos_or_text") and (rep.get("looks_real") or 0) >= 7)
    rep["verdict"] = "pass" if ok else "fail"
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
