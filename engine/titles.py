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
MAX_LEN = 80
_state = {"warned": False, "blocked_until": 0.0, "model": None, "last_error": None, "last_ok": None, "done": 0}

PROMPT_VERSION = 3
PROMPT = """Write a short title for this food & drink story in a game where people bet on whether the fuss
grows or fades. The title must show the FUSS: who is upset, mocking, begging or boycotting, and about what.
Rules:
- 5 to 9 words, plain everyday English, the way people talk
- brand first if there is one
- name the reaction (fans furious, people roast, customers quit, students boycott, fans beg) when the headlines show one
- never turn mockery or complaints into praise; never state a claim from the story as fact
- only facts from the headlines below; invent nothing
- no quotes, no emojis, no hashtags, no full stop
Examples:
  "Taco Bell customers outraged over new packaging they say means less food" -> Taco Bell fans say they get less food
  "People Online Are Joking About The Starbucks Braised Short Rib Grilled Cheese" -> Starbucks' short rib sandwich gets roasted online
  "The Discontinued Jersey Mike's Sub That Customers Want Back" -> Jersey Mike's fans beg for their sub back
  "College students rally in support of Starbucks baristas urging boycott" -> Students call for a Starbucks boycott
Return only the title.

Headline:
{headlines}"""


def configured():
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GROQ_API_KEY"))


GEMINI = "https://generativelanguage.googleapis.com/v1beta"


def _gemini_models(key):
    """Ask Google which models this key can use; newest 'flash' model first.
    (Model names are retired over time, so they are never hard-coded.)"""
    r = requests.get(f"{GEMINI}/models", params={"key": key, "pageSize": 200}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"gemini models HTTP {r.status_code}: {r.text[:120]}")
    names = [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
             if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    flash = [n for n in names if "flash" in n and "thinking" not in n and "image" not in n
             and "tts" not in n and "live" not in n and "exp" not in n]
    lite = [n for n in flash if "lite" in n]
    ranked = sorted(lite, reverse=True) + sorted([n for n in flash if n not in lite], reverse=True)
    return ranked or names


def _gemini(prompt):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    wanted = os.getenv("GEMINI_MODEL")
    tried = []
    models = ([wanted] if wanted else []) + ([_state["model"]] if _state["model"] else []) + _gemini_models(key)[:4]
    for model in dict.fromkeys(m for m in models if m):
        tried.append(model)
        r = requests.post(f"{GEMINI}/models/{model}:generateContent", params={"key": key}, timeout=30,
                          json={"contents": [{"parts": [{"text": prompt}]}],
                                "generationConfig": {"temperature": 0.4, "maxOutputTokens": 60}})
        if r.status_code in (404, 400) and "not found" in r.text.lower():
            continue                                   # retired model → next one
        if r.status_code != 200:
            raise RuntimeError(f"gemini {model} HTTP {r.status_code}: {r.text[:150]}")
        _state["model"] = model
        parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}]
        return "".join(p.get("text", "") for p in parts)
    raise RuntimeError(f"gemini: none of these models answered: {', '.join(tried)}")


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
    # never end on a dangling little word ("…call for boycott over")
    while re.search(r"\s(?:over|for|to|and|of|the|a|an|at|with|in|on|after|as|by|from|about)$", t, re.I):
        t = t.rsplit(" ", 1)[0]
    return t


def _stems(t, drop=()):
    ws = {w for w in re.findall(r"[a-z0-9']+", (t or "").lower().replace("’", "'")) if len(w) > 3}
    return {w[:5] for w in ws if w not in drop}


def matches_headline(short, headline, brand=None):
    """The short title must be about the same thing as the headline (stops the AI inventing a
    different story): same brand as the headline, and at least one shared topic word."""
    from engine.everyday import brand_in
    hb, tb = brand_in([headline]), brand_in([short])
    if hb and (not tb or tb.lower().replace("’", "'") != hb.lower().replace("’", "'")):
        return False                       # wrong or missing brand ("Starbucks…" for a Jersey Mike's story)
    if tb and not hb:
        return False                       # a brand the headline never mentions
    bw = set(re.findall(r"[a-z0-9']+", (hb or "").lower().replace("’", "'")))
    bw |= {w + "'s" for w in bw} | {w.rstrip("s") for w in bw}
    return len(_stems(short, bw) & _stems(headline, bw)) >= 1


def shorten(headline, brand=None):
    """Short title from the story's MAIN headline only (other grouped headlines can be about
    something else and made the AI mix stories up)."""
    prompt = PROMPT.format(headlines=headline)
    raw = _gemini(prompt) if os.getenv("GEMINI_API_KEY") else _groq(prompt)
    t = clean(raw)
    if len(t) < 6:
        raise RuntimeError("empty title")
    if not matches_headline(t, headline, brand):
        raise RuntimeError(f"title doesn't match the headline, kept the original: {t!r}")
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
            if snap.get("title_by") == "moderator":
                continue
            if (snap.get("short_title") or snap.get("title_by") == "ai-skipped") and snap.get("title_v", 1) >= PROMPT_VERSION:
                continue                       # already written with the current instructions
            try:
                snap["short_title"] = shorten(n.title, snap.get("company"))
                _state["last_ok"], _state["done"] = time.time(), _state["done"] + 1
                snap["title_by"], snap["title_v"] = "ai", PROMPT_VERSION
                n.snapshot = json.dumps(snap)
                session.commit()
                done.append(f"{snap['short_title']}")
            except Exception as e:
                print(f"⚠️ short title failed for {n.sid}: {e}")
                if "doesn't match" in str(e):          # show the original headline rather than a wrong title
                    snap.pop("short_title", None)
                    snap["title_by"], snap["title_v"] = "ai-skipped", PROMPT_VERSION
                    n.snapshot = json.dumps(snap)
                    session.commit()
                _state["last_error"] = f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}: {str(e)[:250]}"
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


def status():
    """For /api/titles/status — is the AI working? (never shows the key)"""
    session = SessionLocal()
    try:
        listed = session.query(Narrative).filter(Narrative.status == "LISTED").all()
        with_t = sum(1 for n in listed if json.loads(n.snapshot or "{}").get("short_title"))
    finally:
        session.close()
    return {"key_set": {"GEMINI_API_KEY": bool(os.getenv("GEMINI_API_KEY")), "GROQ_API_KEY": bool(os.getenv("GROQ_API_KEY"))},
            "provider": "gemini" if os.getenv("GEMINI_API_KEY") else "groq" if os.getenv("GROQ_API_KEY") else None,
            "model_in_use": _state["model"], "titles_written_since_restart": _state["done"],
            "paused_minutes_left": max(0, int((_state["blocked_until"] - time.time()) / 60)),
            "last_error": _state["last_error"],
            "listed_stories": len(listed), "listed_with_short_title": with_t}
