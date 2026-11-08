"""
Social buzz recorder — Bluesky (test phase)
===========================================
How much are people POSTING about a story? For listed stories (every 30 min) and the
strongest unlisted candidates (hourly) we search Bluesky for the last 24 hours:
    query = brand + main topic word (e.g. "Starbucks drink"), English posts
and record: number of posts, likes, reposts, replies, and the most-liked posts.

Recorded only — not part of the story score yet. After a week or two we check whether
buzz runs ahead of news coverage; if it does, it can become part of the score.

Login: a free Bluesky account with an APP PASSWORD, read from the environment:
    BLUESKY_HANDLE, BLUESKY_APP_PASSWORD   (never stored in code or the database)
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

import requests

from db.database import SessionLocal
from db.models import Narrative, SocialSignal

PDS = "https://bsky.social/xrpc"
LISTED_EVERY_MIN    = 30
CANDIDATE_EVERY_MIN = 60
CANDIDATES_PER_RUN  = 5
MAX_SEARCHES_RUN    = 12
PAGES               = 3        # up to 300 posts per search
BACKOFF_MIN         = 30
_state = {"jwt": None, "jwt_ts": 0.0, "blocked_until": 0.0, "warned": False}


def configured():
    return bool(os.getenv("BLUESKY_HANDLE") and os.getenv("BLUESKY_APP_PASSWORD"))


def _login(force=False):
    if _state["jwt"] and not force and time.time() - _state["jwt_ts"] < 90 * 60:
        return _state["jwt"]
    r = requests.post(f"{PDS}/com.atproto.server.createSession", timeout=20,
                      json={"identifier": os.getenv("BLUESKY_HANDLE", "").strip().lstrip("@"),
                            "password": os.getenv("BLUESKY_APP_PASSWORD", "").strip()})
    if r.status_code != 200:
        raise RuntimeError(f"login HTTP {r.status_code}: {r.text[:120]}")
    _state["jwt"], _state["jwt_ts"] = r.json()["accessJwt"], time.time()
    return _state["jwt"]


def search(query, hours=24):
    """Posts in the last `hours` matching the query → summary dict."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    posts, cursor = [], None
    for page in range(PAGES):
        params = {"q": query, "limit": 100, "sort": "latest", "since": since, "lang": "en"}
        if cursor:
            params["cursor"] = cursor
        for attempt in (1, 2):
            r = requests.get(f"{PDS}/app.bsky.feed.searchPosts", params=params, timeout=20,
                             headers={"Authorization": f"Bearer {_login(force=attempt == 2)}"})
            if r.status_code == 401 and attempt == 1:
                continue                      # token expired → log in again once
            break
        if r.status_code != 200:
            raise RuntimeError(f"search HTTP {r.status_code}: {r.text[:120]}")
        d = r.json()
        posts += d.get("posts", [])
        cursor = d.get("cursor")
        if not cursor or len(d.get("posts", [])) < 100:
            break
    likes = sum(p.get("likeCount") or 0 for p in posts)
    reposts = sum((p.get("repostCount") or 0) + (p.get("quoteCount") or 0) for p in posts)
    replies = sum(p.get("replyCount") or 0 for p in posts)
    top = sorted(posts, key=lambda p: (p.get("likeCount") or 0) + 2 * (p.get("repostCount") or 0), reverse=True)[:5]
    sample = []
    for p in top:
        handle = (p.get("author") or {}).get("handle", "")
        rkey = (p.get("uri") or "").rsplit("/", 1)[-1]
        sample.append({"text": ((p.get("record") or {}).get("text") or "")[:280], "handle": handle,
                       "likes": p.get("likeCount") or 0, "reposts": p.get("repostCount") or 0,
                       "url": f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else None,
                       "at": p.get("indexedAt")})
    return {"posts": len(posts), "likes": likes, "reposts": reposts, "replies": replies, "sample": sample}


def query_for(title_list, company):
    from engine.everyday import topic_words
    words = topic_words(title_list, company, n=2)
    if company:
        return f"{company} {words[0]}" if words else company
    return " ".join(words) if len(words) >= 2 else None


def _check(session, sid, titles, company):
    q = query_for(titles, company)
    rec = SocialSignal(sid=sid, ts=time.time(), source="bluesky", query=q)
    if not q:
        rec.status, rec.error = "ERROR", "no search words for this story"
    else:
        try:
            res = search(q)
            rec.posts, rec.likes, rec.reposts, rec.replies = res["posts"], res["likes"], res["reposts"], res["replies"]
            rec.sample = json.dumps(res["sample"])
        except Exception as e:
            rec.status, rec.error = "ERROR", str(e)[:300]
            if "HTTP 429" in str(e) or "login" in str(e):
                _state["blocked_until"] = time.time() + BACKOFF_MIN * 60
    session.add(rec)
    session.commit()
    return rec


def run_social_checks():
    if not configured():
        if not _state["warned"]:
            print("💤 Bluesky not configured (BLUESKY_HANDLE / BLUESKY_APP_PASSWORD) — social buzz skipped")
            _state["warned"] = True
        return
    if time.time() < _state["blocked_until"]:
        return
    session = SessionLocal()
    try:
        now = time.time()
        last = {}
        for r in session.query(SocialSignal).filter(SocialSignal.ts >= now - 3 * 3600).all():
            last[r.sid] = max(last.get(r.sid, 0), r.ts)
        jobs = []
        for n in session.query(Narrative).filter(Narrative.status == "LISTED").all():
            if now - last.get(n.sid, 0) >= LISTED_EVERY_MIN * 60:
                snap = json.loads(n.snapshot or "{}")
                titles = [a["title"] for a in snap.get("articles", [])][:20] or [n.title]
                jobs.append((n.sid, titles, snap.get("company")))
        # strongest unlisted candidates of the food & drink arena
        import sqlalchemy as sa
        row = session.execute(sa.text("SELECT story_index FROM arena_readings WHERE arena = 'everyday' "
                                      "ORDER BY ts DESC LIMIT 1")).fetchone()
        if row:
            cands = sorted([x for x in json.loads(row[0]) or [] if not x.get("listed")],
                           key=lambda x: x.get("score") or 0, reverse=True)[:CANDIDATES_PER_RUN]
            for x in cands:
                if now - last.get(x["sid"], 0) >= CANDIDATE_EVERY_MIN * 60:
                    jobs.append((x["sid"], [x["title"]], x.get("company")))
        done = 0
        for sid, titles, company in jobs[:MAX_SEARCHES_RUN]:
            rec = _check(session, sid, titles, company)
            done += 1
            if rec.status == "ERROR" and time.time() < _state["blocked_until"]:
                print(f"⏸ Bluesky paused {BACKOFF_MIN} min: {rec.error}")
                break
            time.sleep(1)
        if done:
            ok = session.query(SocialSignal).filter(SocialSignal.ts >= now).all()
            print(f"🦋 bluesky: {done} stories checked — " +
                  ", ".join(f"{(r.query or '—')[:25]}={r.posts if r.status == 'OK' else 'ERR'}" for r in ok[:6]))
    finally:
        session.close()


def buzz_for(session, sid, moderator=False, hours=48):
    rows = (session.query(SocialSignal).filter(SocialSignal.sid == sid, SocialSignal.ts >= time.time() - hours * 3600)
            .order_by(SocialSignal.ts).all())
    ok = [r for r in rows if r.status == "OK"]
    latest = rows[-1] if rows else None
    out = {"configured": configured(),
           "series": [[r.ts, r.posts] for r in ok],
           "latest": None}
    if latest:
        out["latest"] = {"ts": latest.ts, "query": latest.query, "status": latest.status, "error": latest.error,
                         "posts": latest.posts, "likes": latest.likes, "reposts": latest.reposts,
                         "replies": latest.replies}
        if moderator and latest.sample:
            out["latest"]["sample"] = json.loads(latest.sample)
    return out
