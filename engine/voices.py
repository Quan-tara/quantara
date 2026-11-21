"""
Who spreads a story, and on how many platforms — test phase (21 Nov 2026)
=========================================================================
Two ideas from computational social science, recorded only:

  voices (network)   a story pushed by many independent people is more likely real and lasting than one
                     pushed by a single big account. For the strongest counting stories we read the matching
                     Bluesky posts (read-only, same search words as the buzz check) and record: how many
                     different accounts, and the share of engagement (likes + 2 × reposts) the top 3 carry.
                     At most CHECKS_PER_RUN stories a reading, each again after RECHECK_H hours.
  platforms (bias)   every source sees a different slice of America: news readers, Bluesky users, YouTube
                     viewers, Google searchers. A story seen on several of them is less likely to be one
                     platform's quirk. Breadth = how many of 4 show it (news counts always).

Both are stored on each story warning (forward log 'features'), so the track record can show whether
broad-voice and multi-platform warnings are followed by damage more often.
"""

import json
import time

import requests

from db.models import BehaviourSignal, SocialSignal, StoryVoices

CHECKS_PER_RUN = 3
RECHECK_H = 6
BROAD_AUTHORS = 10            # "many voices"
CONCENTRATED = 0.7            # top 3 accounts carry 70%+ of the engagement


def _search_posts(query, hours=48, pages=2):
    from engine import social as S
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    posts, cursor = [], None
    for _ in range(pages):
        params = {"q": query, "limit": 100, "sort": "latest", "since": since, "lang": "en"}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{S.PDS}/app.bsky.feed.searchPosts", params=params, timeout=20,
                         headers={"Authorization": f"Bearer {S._login()}"})
        if r.status_code != 200:
            raise RuntimeError(f"search HTTP {r.status_code}")
        d = r.json()
        posts += d.get("posts", [])
        cursor = d.get("cursor")
        if not cursor or len(d.get("posts", [])) < 100:
            break
    return posts


def measure(posts):
    """{posts, authors, concentration, top_handle} for a list of Bluesky posts."""
    eng = {}
    for p in posts:
        a = (p.get("author") or {})
        key = a.get("did") or a.get("handle") or "?"
        e = 1 + (p.get("likeCount") or 0) + 2 * ((p.get("repostCount") or 0) + (p.get("quoteCount") or 0))
        h = eng.setdefault(key, {"e": 0, "handle": a.get("handle")})
        h["e"] += e
    total = sum(v["e"] for v in eng.values())
    top = sorted(eng.values(), key=lambda v: -v["e"])
    conc = round(sum(v["e"] for v in top[:3]) / total, 2) if total else None
    return {"posts": len(posts), "authors": len(eng), "concentration": conc,
            "top_handle": top[0]["handle"] if top else None}


def label(authors, conc):
    if not authors:
        return "no posts"
    if conc is not None and conc >= CONCENTRATED:
        return "pushed by a few accounts"
    return "many independent voices" if authors >= BROAD_AUTHORS else "a few voices"


def run_checks(session, rows):
    """Measure voices for the strongest counting stories that are due (recorded only)."""
    from engine import social as S
    if not S.configured() or time.time() < S._state.get("blocked_until", 0):
        return 0
    done = 0
    for r in [x for x in rows if (x.get("weighted") or 0) > 0][:8]:
        if done >= CHECKS_PER_RUN:
            break
        last = (session.query(StoryVoices.ts).filter(StoryVoices.sid == r["sid"])
                .order_by(StoryVoices.ts.desc()).first())
        if last and time.time() - last[0] < RECHECK_H * 3600:
            continue
        terms = S.story_terms([r.get("title")], r.get("company"))
        rec = StoryVoices(sid=r["sid"], ts=time.time())
        if not terms:
            rec.status = "NO_TERMS"
        else:
            try:
                posts = [p for p in _search_posts(f'"{terms[0]}"')
                         if S._matches((p.get("record") or {}).get("text"), terms[1])]
                m = measure(posts)
                rec.posts, rec.authors, rec.concentration, rec.top_handle = (m["posts"], m["authors"],
                                                                             m["concentration"], m["top_handle"])
            except Exception as e:
                rec.status = "ERROR"
                print(f"⚠️ voices: {e}")
        session.add(rec)
        done += 1
    session.commit()
    return done


def attach(session, rows, now=None):
    """Adds r['voices'], r['concentration'], r['platforms'] to the rows (latest known values)."""
    now = now or time.time()
    sids = [r["sid"] for r in rows] or ["-"]
    vo = {}
    for v in (session.query(StoryVoices).filter(StoryVoices.sid.in_(sids), StoryVoices.status == "OK")
              .order_by(StoryVoices.ts).all()):
        vo[v.sid] = v
    bsky, yt = {}, {}
    for s in (session.query(SocialSignal).filter(SocialSignal.sid.in_(sids), SocialSignal.status == "OK",
                                                 SocialSignal.ts >= now - 3 * 86400).order_by(SocialSignal.ts).all()):
        (bsky if s.source == "bluesky" else yt)[s.sid] = s.posts or 0
    acted = {}
    for b in (session.query(BehaviourSignal).filter(BehaviourSignal.sid.in_(sids), BehaviourSignal.status == "OK",
                                                    BehaviourSignal.ts >= now - 3 * 86400).all()):
        try:
            from engine.behaviour import judge
            acted[b.sid] = acted.get(b.sid) or judge(json.loads(b.result or "{}"))
        except Exception:
            pass
    for r in rows:
        v = vo.get(r["sid"])
        r["voices"] = v.authors if v else None
        r["concentration"] = v.concentration if v else None
        r["platforms"] = 1 + (bsky.get(r["sid"], 0) >= 3) + (yt.get(r["sid"], 0) >= 3) + bool(acted.get(r["sid"]))
    return rows


def for_story(session, sid):
    v = (session.query(StoryVoices).filter(StoryVoices.sid == sid, StoryVoices.status == "OK")
         .order_by(StoryVoices.ts.desc()).first())
    if not v:
        return None
    return {"ts": v.ts, "posts": v.posts, "authors": v.authors, "concentration": v.concentration,
            "top_handle": v.top_handle, "label": label(v.authors, v.concentration)}
