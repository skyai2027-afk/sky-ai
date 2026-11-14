// Sky X assistant – Telegram bot that DRAFTS tweets and replies for X.
// It never posts to X itself: the owner copies and posts manually.

const PERSONA_URL = "https://raw.githubusercontent.com/skyai2027-afk/sky-ai/main/persona.md";
const MODELS = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.5-flash", "gemini-flash-lite-latest"];

const HELP = `🐦 Sky X, רק בשביל X (טוויטר)
אני מנסחת, אתה מפרסם ב-X ידנית.

✍️ "ציוץ": 3 הצעות לציוץ
✍️ "ציוץ על ...": 3 הצעות על נושא מסוים (למשל "ציוץ על הניצחון 3-1 על ריאל". את העובדות אתה נותן לי)
💬 שולחים טקסט של ציוץ: 3 הצעות לתגובה
📸 שולחים צילום מסך של ציוץ: 3 הצעות לתגובה
🧠 "הגבתי: ..." או "פרסמתי: ...": אני לומדת מהסגנון שלך
🗑️ "/forget": שוכחת את כל מה שלמדתי

כל בוקר אשלח 3 רעיונות לציוצים של היום.`;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/telegram") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET)
      return new Response("forbidden", { status: 403 });
    const update = await request.json();
    // Telegram re-sends an update if we answer slowly: skip ones we already handled
    const last = Number((await env.SKY_X.get("last_update_id")) || 0);
    if (update.update_id && update.update_id <= last) return new Response("dup");
    if (update.update_id) await env.SKY_X.put("last_update_id", String(update.update_id));
    try {
      await handle(update, env);
    } catch (e) {
      await send(env, `❌ תקלה: ${String(e).slice(0, 300)}`);
    }
    return new Response("ok");
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(morningIdeas(env).catch((e) => send(env, `❌ תקלה ברעיונות הבוקר: ${String(e).slice(0, 300)}`)));
  },
};

// ---------------- Telegram ----------------
async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TG_TOKEN}/${method}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return r.json();
}
const send = (env, text) => tg(env, "sendMessage", { chat_id: env.OWNER_CHAT_ID, text: text.slice(0, 4000) });

async function sendOptions(env, title, options) {
  await send(env, title);
  // each option as its own message = easy long-press -> copy
  for (const o of options) await send(env, o);
}

async function photoBase64(env, fileId) {
  const info = await tg(env, "getFile", { file_id: fileId });
  const r = await fetch(`https://api.telegram.org/file/bot${env.TG_TOKEN}/${info.result.file_path}`);
  const bytes = new Uint8Array(await r.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

// ---------------- memory (owner's own tweets/replies = style examples) ----------------
async function getExamples(env) {
  return JSON.parse((await env.SKY_X.get("examples")) || "[]");
}
async function addExample(env, text) {
  const ex = await getExamples(env);
  ex.push(text.slice(0, 400));
  await env.SKY_X.put("examples", JSON.stringify(ex.slice(-40)));
  return ex.length;
}

// ---------------- Gemini ----------------
async function persona(env) {
  const cached = await env.SKY_X.get("persona_cache");
  if (cached) return cached;
  const text = await (await fetch(PERSONA_URL)).text();
  await env.SKY_X.put("persona_cache", text, { expirationTtl: 3600 });
  return text;
}

function parseJson(text) {
  const m = text.match(/\{[\s\S]*\}/);
  return JSON.parse(m ? m[0] : text);
}

async function gemini(env, prompt, { image = null, search = false } = {}) {
  const parts = [{ text: prompt }];
  if (image) parts.push({ inline_data: { mime_type: "image/jpeg", data: image } });
  const body = { contents: [{ parts }], generationConfig: { temperature: 1.0 } };
  if (search) body.tools = [{ google_search: {} }];
  else body.generationConfig.responseMimeType = "application/json";
  let last;
  for (const model of MODELS) {
    try {
      const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-goog-api-key": env.GEMINI_API_KEY },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(30000),
      });
      if (!r.ok) throw new Error(`${model} ${r.status}: ${(await r.text()).slice(0, 150)}`);
      const d = await r.json();
      const text = d.candidates[0].content.parts.map((p) => p.text || "").join("");
      return parseJson(text);
    } catch (e) {
      last = e;
    }
  }
  throw last;
}

async function basePrompt(env) {
  const ex = await getExamples(env);
  const examples = ex.length ? ex.slice(-15).map((e) => `- ${e}`).join("\n") : "(none yet)";
  return `You write for the X (Twitter) account of Sky Fiori, an AI-generated virtual influencer.
Follow her persona bible, and ESPECIALLY section 12 "Sky ב-X" (louder, sharper, more cynical version):
<persona>
${await persona(env)}
</persona>
Real examples the owner wrote/approved for X (match this voice closely):
${examples}

Hard rules: cynicism at situations, never insults at people; no politics/religion; no slurs;
never invent scores, quotes or news (only use facts you verified with search, otherwise write an opinion);
max 280 characters each; at most one hashtag; English unless the topic is Israeli or the owner writes Hebrew.`;
}

// ---------------- actions ----------------
async function tweets(env, topic) {
  const p = `${await basePrompt(env)}

Write 3 different tweet options${topic ? ` about: "${topic}"` : " for today"}.
${topic ? "" : "Do not refer to specific real events you are not sure about (scores, transfers, news). Use evergreen fan life, tennis, Tel Aviv life, food, being an AI."}
Today's date: ${new Date().toISOString().slice(0, 10)}.
Return ONLY JSON: {"tweets": ["...", "...", "..."], "context_he": ""}`;
  const r = await gemini(env, p);
  return r;
}

async function replies(env, tweetText, image) {
  const p = `${await basePrompt(env)}

Someone posted this on X${image ? " (see the screenshot)" : ""}:
${tweetText ? `"${tweetText}"` : ""}
Write 3 different reply options from Sky. Short, witty, adds something to the conversation, fits her voice.
If the tweet is hateful/political/bait, make the options light and non-toxic, or say so in note_he.
Return ONLY JSON: {"replies": ["...", "...", "..."], "note_he": "optional short Hebrew note for the owner, or empty"}`;
  return gemini(env, p, { image });
}

async function morningIdeas(env) {
  const r = await tweets(env, "");
  await sendOptions(env, `☀️ 3 רעיונות לציוצים של היום${r.context_he ? `\n📰 ${r.context_he}` : ""}\n(לוחצים לחיצה ארוכה על אחד ← העתקה ← מדביקים ב-X)`,
    r.tweets || []);
}

async function handle(update, env) {
  const msg = update.message;
  if (!msg || String(msg.chat.id) !== String(env.OWNER_CHAT_ID)) return;
  const text = (msg.text || msg.caption || "").trim();

  if (text === "/start" || text === "/help") return send(env, HELP);
  if (text === "/forget") {
    await env.SKY_X.put("examples", "[]");
    return send(env, "🗑️ שכחתי. מתחילה ללמוד את הסגנון מחדש.");
  }
  const learn = text.match(/^(הגבתי|פרסמתי|צייצתי)\s*[:：]\s*([\s\S]+)/);
  if (learn) {
    const n = await addExample(env, learn[2].trim());
    return send(env, `🧠 שמרתי. יש לי ${n} דוגמאות לסגנון שלך.`);
  }
  const tw = text.match(/^(ציוץ|\/tweet)\s*(על)?\s*([\s\S]*)/);
  if (tw && !msg.photo) {
    await tg(env, "sendChatAction", { chat_id: env.OWNER_CHAT_ID, action: "typing" });
    const r = await tweets(env, tw[3].trim());
    return sendOptions(env, `✍️ 3 הצעות לציוץ${r.context_he ? `\n📰 ${r.context_he}` : ""}`, r.tweets || []);
  }
  if (msg.photo || text) {
    await tg(env, "sendChatAction", { chat_id: env.OWNER_CHAT_ID, action: "typing" });
    const image = msg.photo ? await photoBase64(env, msg.photo[msg.photo.length - 1].file_id) : null;
    const r = await replies(env, text, image);
    return sendOptions(env, `💬 3 הצעות לתגובה${r.note_he ? `\n📝 ${r.note_he}` : ""}`, r.replies || []);
  }
}
