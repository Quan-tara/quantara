"""
Social buzz recorder — YouTube (test phase)
===========================================
How many people are making and WATCHING videos about a story? For listed stories
(every 3 h) and the strongest unlisted candidates (every 6 h) we search YouTube for
videos from the last 3 days:
    query = "<Company>" + main topic word   (same rule as Bluesky; stories without a
                                              clear company are skipped)
and record: number of videos, total views, likes, comments, and the most-viewed videos.

Stored in social_signals with source = "youtube":
    posts = videos, reposts = views, likes = likes, replies = comments

Free quota: 10,000 units a day. One search = 100 units, the statistics lookup = 1.
We stay under DAILY_UNITS (counted from today's rows in the database, so a restart
doesn't reset it). Recorded only — not part of the story score.

Key: a free YouTube Data API v3 key, read from the environment:
    YOUTUBE_API_KEY   (never stored in code or the database)
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

import requests

from db.database import SessionLocal
from db.models import Narrative, SocialSignal

API = "https://www.googleapis.com/youtube/v3"
LISTED_EVERY_MIN    = 180
CANDIDATE_EVERY_MIN = 360
CANDIDATES_PER_RUN  = 3
MAX_SEARCHES_RUN    = 6
WINDOW_HOURS        = 72
DAILY_UNITS         = 8000      # of the free 10,000 — leaves room for mistakes
UNITS_PER_CHECK     = 101
BACKOFF_MIN         = 360
_state = {"blocked_until": 0.0, "warned": False, "last_error": None}


def configured():
    return bool(os.getenv("YOUTUBE_API_KEY"))


def _get(path, params):
    params = dict(params, key=os.getenv("YOUTUBE_API_KEY", "").strip())
    r = requests.get(f"{API}/{path}", params=params, timeout=20)
    if r.status_code != 200:
        reason = ""
        try:
            reason = r.json()["error"]["errors"][0].get("reason", "")
        except Exception:
            pass
        raise RuntimeError(f"youtube HTTP {r.status_code} {reason}".strip())
    return r.json()


def search(query, must=None):
    """Videos from the last WINDOW_HOURS matching the query → summary dict.
    must = story words: only videos whose title or description contains one are counted."""
    after = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = _get("search", {"part": "id", "q": query, "type": "video", "maxResults": 50,
                        "publishedAfter": after, "relevanceLanguage": "en", "order": "relevance"})
    ids = [it["id"]["videoId"] for it in d.get("items", []) if it.get("id", {}).get("videoId")]
    vids = []
    if ids:
        v = _get("videos", {"part": "snippet,statistics", "id": ",".join(ids)})
        vids = v.get("items", [])
    if must:
        from engine.social import _matches
        vids = [x for x in vids if _matches(((x.get("snippet") or {}).get("title") or "") + " " +
                                            ((x.get("snippet") or {}).get("description") or ""), must)]
    num = lambda x, k: int((x.get("statistics") or {}).get(k) or 0)
    views = sum(num(x, "viewCount") for x in vids)
    likes = sum(num(x, "likeCount") for x in vids)
    comments = sum(num(x, "commentCount") for x in vids)
    top = sorted(vids, key=lambda x: num(x, "viewCount"), reverse=True)[:5]
    sample = [{"text": (x.get("snippet") or {}).get("title", "")[:200],
               "handle": (x.get("snippet") or {}).get("channelTitle", ""),
               "views": num(x, "viewCount"), "likes": num(x, "likeCount"),
               "comments": num(x, "commentCount"),
               "url": f"https://www.youtube.com/watch?v={x['id']}",
               "at": (x.get("snippet") or {}).get("publishedAt")} for x in top]
    return {"videos": len(vids), "views": views, "likes": likes, "comments": comments, "sample": sample}


def _units_today(session):
    day0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    n = session.query(SocialSignal).filter(SocialSignal.source == "youtube", SocialSignal.ts >= day0,
                                           SocialSignal.query.isnot(None)).count()
    return n * UNITS_PER_CHECK


def _check(session, sid, query, terms):
    rec = SocialSignal(sid=sid, ts=time.time(), source="youtube", query=query)
    try:
        company, words = terms
        res = search(f'"{company}" {words[0]}', must=words)
        rec.posts, rec.reposts, rec.likes, rec.replies = res["videos"], res["views"], res["likes"], res["comments"]
        rec.sample = json.dumps(res["sample"])
    except Exception as e:
        rec.status, rec.error = "ERROR", str(e)[:300]
        if "403" in str(e) or "429" in str(e) or "quota" in str(e).lower():
            _state["blocked_until"] = time.time() + BACKOFF_MIN * 60
        _state["last_error"] = rec.error
    session.add(rec)
    session.commit()
    return rec


def run_youtube_checks():
    if not configured():
        if not _state["warned"]:
            print("💤 YouTube not configured (YOUTUBE_API_KEY) — video buzz skipped")
            _state["warned"] = True
        return
    if time.time() < _state["blocked_until"]:
        left = int((_state["blocked_until"] - time.time()) / 60)
        print(f"⏸ youtube paused for {left} more min — last error: {_state['last_error']}")
        return
    from engine.social import query_for, story_terms
    session = SessionLocal()
    try:
        now = time.time()
        used = _units_today(session)
        room = max(0, (DAILY_UNITS - used) // UNITS_PER_CHECK)
        if not room:
            print(f"▶️ youtube: daily quota used ({used}/{DAILY_UNITS}) — next checks tomorrow (UTC)")
            return
        last = {}
        for r in session.query(SocialSignal).filter(SocialSignal.source == "youtube",
                                                    SocialSignal.ts >= now - 12 * 3600).all():
            last[r.sid] = max(last.get(r.sid, 0), r.ts)
        jobs = []
        for n in session.query(Narrative).filter(Narrative.status == "LISTED").all():
            if now - last.get(n.sid, 0) >= LISTED_EVERY_MIN * 60:
                snap = json.loads(n.snapshot or "{}")
                titles = [a["title"] for a in snap.get("articles", [])][:20] or [n.title]
                jobs.append((n.sid, titles, snap.get("company")))
        import sqlalchemy as sa
        row = session.execute(sa.text("SELECT story_index FROM arena_readings WHERE arena = 'everyday' "
                                      "ORDER BY ts DESC LIMIT 1")).fetchone()
        if row:
            cands = sorted([x for x in json.loads(row[0]) or [] if not x.get("listed")],
                           key=lambda x: x.get("score") or 0, reverse=True)
            k = 0
            for x in cands:
                if k >= CANDIDATES_PER_RUN:
                    break
                if not query_for([x["title"]], x.get("company")):
                    continue
                k += 1
                if now - last.get(x["sid"], 0) >= CANDIDATE_EVERY_MIN * 60:
                    jobs.append((x["sid"], [x["title"]], x.get("company")))
        jobs = [(sid, query_for(t, c), story_terms(t, c)) for sid, t, c in jobs]
        jobs = [j for j in jobs if j[2]][:min(MAX_SEARCHES_RUN, room)]
        done = []
        for sid, q, terms in jobs:
            rec = _check(session, sid, q, terms)
            done.append(rec)
            if rec.status == "ERROR" and time.time() < _state["blocked_until"]:
                print(f"⏸ YouTube paused {BACKOFF_MIN // 60} h: {rec.error}")
                break
            time.sleep(0.5)
        if not done:
            print(f"▶️ youtube: nothing due this round | quota today ≈{used}/{DAILY_UNITS}")
        if done:
            print(f"▶️ youtube: {len(done)} stories checked — " +
                  ", ".join(f"{(r.query or '—')[:25]}={r.posts}v/{r.reposts}views" if r.status == "OK"
                            else f"{(r.query or '—')[:25]}=ERR" for r in done[:6]) +
                  f" | quota today ≈{_units_today(session)}/{DAILY_UNITS}")
    finally:
        session.close()


def video_buzz_for(session, sid, moderator=False, hours=96):
    rows = (session.query(SocialSignal).filter(SocialSignal.sid == sid, SocialSignal.source == "youtube",
                                               SocialSignal.ts >= time.time() - hours * 3600)
            .order_by(SocialSignal.ts).all())
    ok = [r for r in rows if r.status == "OK"]
    latest = rows[-1] if rows else None
    out = {"configured": configured(), "series": [[r.ts, r.reposts] for r in ok], "latest": None}
    if latest:
        out["latest"] = {"ts": latest.ts, "query": latest.query, "status": latest.status, "error": latest.error,
                         "videos": latest.posts, "views": latest.reposts, "likes": latest.likes,
                         "comments": latest.replies}
        if moderator and latest.sample:
            out["latest"]["sample"] = json.loads(latest.sample)
    return out


def status(session):
    """For /api/youtube/status — is the recorder working, and what did it last see?"""
    rows = (session.query(SocialSignal).filter(SocialSignal.source == "youtube")
            .order_by(SocialSignal.ts.desc()).limit(10).all())
    return {"configured": configured(), "quota_today": _units_today(session), "daily_limit": DAILY_UNITS,
            "paused_minutes_left": max(0, int((_state["blocked_until"] - time.time()) / 60)),
            "last_error": _state["last_error"],
            "recent": [{"at": datetime.fromtimestamp(r.ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                        "query": r.query, "status": r.status, "error": r.error,
                        "videos": r.posts, "views": r.reposts} for r in rows]}
