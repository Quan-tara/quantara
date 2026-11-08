"""
Short titles — make listed stories easy to react to
===================================================
News headlines are long and written for search engines ("Taco Bell customers outraged over new
packaging they say means less food for the same price"). For every LISTED story a free AI
model writes a short hook ("Taco Bell: less food, same price?"). The original headline stays
on the page under it. A moderator can replace the short title by hand at any time; a
hand-written title is never overwritten.

Free AI providers (whichever key is set in Render's environment; never stored in code):
  GEMINI_API_KEY  — Google AI Studio (free tier)    model: GEMINI_MODEL (default gemini-2.0-flash)
  GROQ_API_KEY    — Groq (free tier)                model: GROQ_MODEL   (default llama-3.1-8b-instant)
Without a key nothing happens and the original headline is shown.
"""

import json
import os
import re
import time

import requests

from db.database import SessionLocal
from db.models import Narrative

MAX_PER_RUN = 5
MAX_LEN = 60
_state = {"warned": False, "blocked_until": 0.0}

PROMPT = """Rewrite this food & drink news story as a short title for a social prediction game.
Rules:
- at most 7 words, plain everyday English, the way people talk
- put the brand first if there is one
- a question is fine if it fits
- only use facts that are in the headlines below; do not invent anything; no accusations stated as fact
- no quotes, no emojis, no hashtags, no full stop at the end
Return only the title.

Brand: {brand}
Headlines:
{headlines}"""


def configured():
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GROQ_API_KEY"))


def _gemini(prompt):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    for model in [os.getenv("GEMINI_MODEL", "gemini-2.0-flash"), "gemini-1.5-flash"]:
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          params={"key": key}, timeout=30,
                          json={"contents": [{"parts": [{"text": prompt}]}],
                                "generationConfig": {"temperature": 0.4, "maxOutputTokens": 40}})
        if r.status_code == 404:
            continue                                   # model name retired → try the next one
        if r.status_code != 200:
            raise RuntimeError(f"gemini HTTP {r.status_code}")
        parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}]
        return parts[0].get("text", "")
    raise RuntimeError("gemini: no model available")


def _groq(prompt):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=30,
                      headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '').strip()}"},
                      json={"model": os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"), "temperature": 0.4,
                            "max_tokens": 40, "messages": [{"role": "user", "content": prompt}]})
    if r.status_code != 200:
        raise RuntimeError(f"groq HTTP {r.status_code}")
    return r.json()["choices"][0]["message"]["content"]


def clean(text):
    t = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    t = re.sub(r"^(title|short title)\s*:\s*", "", t, flags=re.I)
    t = t.strip().strip('"“”\'‘’*#').strip().rstrip(".")
    t = re.sub(r"\s+", " ", t)
    if len(t) > MAX_LEN:
        t = t[:MAX_LEN].rsplit(" ", 1)[0]
    return t


def shorten(headlines, brand=None):
    prompt = PROMPT.format(brand=brand or "none", headlines="\n".join(f"- {h}" for h in headlines[:4]))
    raw = _gemini(prompt) if os.getenv("GEMINI_API_KEY") else _groq(prompt)
    t = clean(raw)
    if len(t) < 6:
        raise RuntimeError("empty title")
    return t


def run_titles():
    if not configured():
        if not _state["warned"]:
            print("💤 Short titles off (no GEMINI_API_KEY / GROQ_API_KEY) — original headlines shown")
            _state["warned"] = True
        return
    if time.time() < _state["blocked_until"]:
        return
    session = SessionLocal()
    done = []
    try:
        for n in session.query(Narrative).filter(Narrative.status == "LISTED").all():
            if len(done) >= MAX_PER_RUN:
                break
            snap = json.loads(n.snapshot or "{}")
            if snap.get("short_title") or snap.get("title_by") == "moderator":
                continue
            heads = [n.title] + [a["title"] for a in snap.get("articles", []) if a.get("title") != n.title][:3]
            try:
                snap["short_title"] = shorten(heads, snap.get("company"))
                snap["title_by"] = "ai"
                n.snapshot = json.dumps(snap)
                session.commit()
                done.append(f"{snap['short_title']}")
            except Exception as e:
                print(f"⚠️ short title failed for {n.sid}: {e}")
                if "429" in str(e) or "403" in str(e) or "401" in str(e):
                    _state["blocked_until"] = time.time() + 3600
                    break
        if done:
            print(f"✏️ short titles: " + " | ".join(done))
    finally:
        session.close()


def set_title(sid, moderator_id, title):
    """Moderator writes the short title by hand (empty = let the AI write it again)."""
    from engine.story_comments import is_moderator
    if not is_moderator(moderator_id):
        raise PermissionError("Moderators only")
    session = SessionLocal()
    try:
        n = session.query(Narrative).filter(Narrative.sid == sid).first()
        if not n:
            raise LookupError("Story not found")
        snap = json.loads(n.snapshot or "{}")
        t = clean(title)
        if t:
            snap["short_title"], snap["title_by"] = t, "moderator"
        else:
            snap.pop("short_title", None)
            snap.pop("title_by", None)
        n.snapshot = json.dumps(snap)
        session.commit()
        return {"sid": sid, "short_title": snap.get("short_title"), "title_by": snap.get("title_by")}
    finally:
        session.close()
