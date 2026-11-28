"""
Attention layer — test phase (21 Nov 2026)
==========================================
What pulls people's attention to a food brand often comes BEFORE anything we'd call pressure:
a creator's video, a brand being laughed at, a new creator brand nobody had on the list.

  brands that grow   a relevant headline (or video title) naming no known brand is asked to the AI once:
                     "which brand is this about?". A name seen in 2+ separate stories becomes an active
                     brand and is used everywhere like a listed one (gates, grouping, Bluesky, players).
  YouTube discovery  every 2 hours one search for food-drama videos of the last 48 h (≈ 1,200 of the
                     8,000 daily quota units). A big video about a brand (50k+ views) is recorded and
                     triggers a news search for it — so the story can be listed as soon as outlets cover it.
                     A video alone never lists a story or counts in the index.
  ridicule           roasts, memes and viral jokes about a brand stay OUT of the index, but are recorded.
  forward log        'attention' warnings (one per brand per week): a video with 200k+ views, a ridicule
                     story with score 15+, a new brand. The track record checks whether attention comes
                     before damage. Only then could it ever count.

Method start: the rules changed today, so the 'normal' level, the forward log's main numbers and the
narratives count only from METHOD_VERSION's start (stored the first time this version runs).
"""

import json
import re
import time

from db.models import AttentionItem, BrandAsk, LearnedBrand, MethodStart, SocialSignal

DAY = 86400
METHOD_VERSION = "2026-11-21-wide"
LEARN_AI_PER_RUN = 2
ACTIVE_HITS = 2
YT_EVERY_S = 2 * 3600
YT_WINDOW_H = 72
VIDEO_MIN_VIEWS = 50_000
VIDEO_WARN_VIEWS = 200_000
RIDICULE_WARN_SCORE = 15.0
YT_QUERIES = [
    "found mold in food brand", "fast food exposed", "fast food price outrage", "snack brand controversy",
    "MrBeast Feastables Lunchly", "Prime drink controversy", "restaurant chain backlash", "worst fast food experience",
    "food recall viral", "fast food worker viral video", "grocery store prices viral", "drink brand lawsuit",
    # 28 Nov 2026: creator food drama (the reviews people actually talk about)
    "food reviewer exposes restaurant", "honest fast food review worst", "fast food ranked worst",
    "food influencer controversy", "tiktok food drama", "I tried the viral fast food", "restaurant review gone wrong",
    "fast food portions smaller now", "snack tastes different now", "new menu item review disgusting",
]
RIDICULE_RX = re.compile(r"\broast(?:ed|ing|s)?\b|\bjok(?:e|es|ing)\b|\bmemes?\b|\bmock(?:ed|ing|s)?\b|\bridicul(?:ed|ing)\b|"
                         r"\bclowned\b|\bdragged\b|\btrolled\b|\blaughing at\b|\bwheezing\b|\bcan'?t stop (?:laughing|joking)|"
                         r"\bhilarious\b|\bcursed\b|\bcringe\b|\bembarrass|\bpeople online are\b|\binternet (?:is|can'?t)\b", re.I)
_method = {}


# ─────────────────────────────────────────────
# METHOD START
# ─────────────────────────────────────────────
def method_start(session):
    if METHOD_VERSION in _method:
        return _method[METHOD_VERSION]
    row = session.query(MethodStart).filter(MethodStart.version == METHOD_VERSION).first()
    if not row:
        row = MethodStart(version=METHOD_VERSION, ts=time.time())
        session.add(row)
        session.commit()
        print(f"📏 Measuring restarts with rules {METHOD_VERSION}")
    _method[METHOD_VERSION] = row.ts
    return row.ts


# ─────────────────────────────────────────────
# BRANDS THAT GROW
# ─────────────────────────────────────────────
_BRAND_PROMPT = """A US food & drink news headline or video title: "{title}"
Which company or consumer brand (food, drink, snack, restaurant, grocery) is it mainly about?
Not a person, place or generic food. Answer JSON only: {{"brand": "<name as written>" or null, "us": true/false}}"""


def refresh(session):
    """Put the active learned brands into the brand lists the whole code uses."""
    from engine import everyday as E
    names = [r.name for r in session.query(LearnedBrand).filter(LearnedBrand.status == "active").all()]
    added = [n for n in names if n not in E.FOOD_BRANDS and n not in E.GROCERS]
    E.FOOD_BRANDS.extend(added)
    return added


def _clean_name(name):
    n = re.sub(r"\s+", " ", (name or "").strip().strip("\"'.,")).lower().replace("’", "'")
    if not (2 <= len(n) <= 40) or n in ("null", "none", "unknown"):
        return ""
    return n


def learn_from(session, titles, scores=None):
    """titles: relevant headlines/video titles where no known brand was found. Asks the AI about at most
    LEARN_AI_PER_RUN new ones; returns the brands that just became active."""
    from engine.narratives import ask_json
    from engine.everyday import NON_US_BRANDS, brand_in
    scores = scores or {}
    now, asked, newly = time.time(), 0, []
    for t in dict.fromkeys(x for x in titles if x):
        k = t[:300]
        if session.query(BrandAsk.id).filter(BrandAsk.title == k).first():
            continue
        if brand_in([t]):
            continue
        if asked >= LEARN_AI_PER_RUN:
            break
        asked += 1
        got = ask_json(_BRAND_PROMPT.format(title=t.replace('"', "'")[:300]), 40)
        if got is None:
            continue                                      # AI unavailable: try again next run
        name = _clean_name(got.get("brand")) if got.get("us") else ""
        session.add(BrandAsk(title=k, brand=name or None, ts=now))
        session.flush()
        from engine.everyday import FOOD_BRANDS, GROCERS
        if not name or name in NON_US_BRANDS or name in FOOD_BRANDS or name in GROCERS or brand_in([name]):
            continue
        b = session.query(LearnedBrand).filter(LearnedBrand.name == name).first()
        if not b:
            b = LearnedBrand(name=name, shown=(got.get("brand") or "").strip()[:60], hits=0, first_seen=now,
                             last_seen=now, status="watch", example=t[:300])
            session.add(b)
            session.flush()                               # visible to the next headline in this same run
        b.hits = (b.hits or 0) + 1
        b.last_seen = now
        if b.status != "active" and b.hits >= ACTIVE_HITS:
            b.status = "active"
            newly.append(b.shown or name)
            _item(session, "new brand", name, f"New brand on the list: {b.shown or name}", None, b.hits, "AI", "nb:" + name)
            _warn(session, name, f"new brand: {b.shown or name} — {t[:200]}", "new brand")
    session.commit()
    if newly:
        refresh(session)
        print(f"🆕 New brands on the list: {', '.join(newly)}")
    return newly


# ─────────────────────────────────────────────
# RECORDING
# ─────────────────────────────────────────────
def _item(session, kind, brand, title, url, size, source, key):
    old = session.query(AttentionItem).filter(AttentionItem.key == key).first() if key else None
    if old:
        old.size = max(old.size or 0, size or 0)
        return old
    it = AttentionItem(ts=time.time(), kind=kind, brand=brand, title=(title or "")[:500], url=url,
                       size=size, source=(source or "")[:80], key=(key or "")[:120] or None)
    session.add(it)
    return it


def _warn(session, brand, title, label):
    """Forward log 'attention' — one per brand per week, only for brands the track record can check."""
    try:
        from db.models import ForwardLog
        from engine.reality import DEDUPE_D, _dup_key, brand_key
        b = brand_key(brand)
        if not b:
            return
        now = time.time()
        known = {_dup_key(x, t) for (x, t) in session.query(ForwardLog.brand, ForwardLog.title)
                 .filter(ForwardLog.kind == "attention", ForwardLog.ts >= now - DEDUPE_D * DAY).all()}
        if _dup_key(b, title) in known:
            return
        session.add(ForwardLog(ts=now, kind="attention", brand=b, title=(title or "")[:300], label=label))
    except Exception as e:
        print(f"⚠️ attention log error: {e}")


def record_rows(session, rows):
    """Called each index reading with all story rows: ridicule + brand learning. Recorded only."""
    from engine.everyday import brand_in
    for r in rows:
        t = r.get("title") or ""
        b = (r.get("company") or brand_in([t]) or "").strip()
        if b and RIDICULE_RX.search(t):
            _item(session, "ridicule", b.lower(), t, r.get("url"), r.get("score"), r.get("source"), "rd:" + t[:110])
            if (r.get("score") or 0) >= RIDICULE_WARN_SCORE:
                _warn(session, b, t, "ridicule")
    session.commit()
    unknown = [r["title"] for r in rows if r.get("gate") in ("no US brand or US place named", "no brand and not about food")
               and (r.get("score") or 0) >= 8]
    learn_from(session, unknown)


# ─────────────────────────────────────────────
# YOUTUBE DISCOVERY
# ─────────────────────────────────────────────
_yt = {"last": 0.0, "pos": 0}


def run_youtube_discovery():
    from db.database import SessionLocal
    from engine import youtube as Y
    if not Y.configured() or time.time() < Y._state["blocked_until"] or time.time() - _yt["last"] < YT_EVERY_S:
        return
    session = SessionLocal()
    try:
        if Y._units_today(session) + Y.UNITS_PER_CHECK > Y.DAILY_UNITS:
            return
        _yt["last"] = time.time()
        q = YT_QUERIES[_yt["pos"] % len(YT_QUERIES)]
        _yt["pos"] += 1
        rec = SocialSignal(sid="ytdiscover", ts=time.time(), source="youtube", query=q[:200])
        try:
            from datetime import datetime, timedelta, timezone
            after = (datetime.now(timezone.utc) - timedelta(hours=YT_WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
            d = Y._get("search", {"part": "id", "q": q, "type": "video", "maxResults": 25, "publishedAfter": after,
                                  "relevanceLanguage": "en", "order": "relevance"})
            ids = [it["id"]["videoId"] for it in d.get("items", []) if it.get("id", {}).get("videoId")]
            vids = Y._get("videos", {"part": "snippet,statistics", "id": ",".join(ids)}).get("items", []) if ids else []
            rec.posts = len(vids)
        except Exception as e:
            rec.status, rec.error = "ERROR", str(e)[:300]
            session.add(rec)
            session.commit()
            print(f"⚠️ youtube discovery: {e}")
            return
        session.add(rec)
        n_big, unknown = 0, []
        from engine.everyday import brand_in, english_only, FOOD_WORDS
        import engine.narrative_index as ni
        for v in vids:
            sn = v.get("snippet") or {}
            title, views = sn.get("title") or "", int((v.get("statistics") or {}).get("viewCount") or 0)
            if not english_only(title):
                continue
            b = brand_in([title])
            if not b:
                if views >= VIDEO_MIN_VIEWS and ni._has_any(title.lower(), FOOD_WORDS):
                    unknown.append(title)
                continue
            if views < VIDEO_MIN_VIEWS:
                continue
            n_big += 1
            _item(session, "video", b.lower(), title, f"https://www.youtube.com/watch?v={v['id']}", views,
                  sn.get("channelTitle"), "yt:" + v["id"])
            if views >= VIDEO_WARN_VIEWS:
                _warn(session, b, title, "video")
        session.commit()
        learn_from(session, unknown)
        print(f"📺 youtube discovery '{q}': {len(vids)} videos, {n_big} big ones about a known brand")
    finally:
        session.close()


def news_triggers(session, hours=12, limit=2):
    """Big brand videos of the last hours → [{'brand', 'words'}] for a Google News search."""
    from engine.discovery import _topic
    rows = (session.query(AttentionItem).filter(AttentionItem.kind == "video", AttentionItem.ts >= time.time() - hours * 3600)
            .order_by(AttentionItem.size.desc()).limit(limit * 3).all())
    out, seen = [], set()
    for r in rows:
        if r.brand in seen:
            continue
        seen.add(r.brand)
        out.append({"brand": r.brand, "words": _topic([r.title] * 3, r.brand)[:2]})
        if len(out) >= limit:
            break
    return out


# ─────────────────────────────────────────────
# BOARD (moderators)
# ─────────────────────────────────────────────
def board(session, hours=72):
    since = time.time() - hours * 3600
    items = session.query(AttentionItem).filter(AttentionItem.ts >= since).order_by(AttentionItem.ts.desc()).all()
    pick = lambda k, key: sorted([i for i in items if i.kind == k], key=key)[:8]
    row = lambda i: {"kind": i.kind, "brand": i.brand, "title": i.title, "url": i.url, "size": i.size,
                     "source": i.source, "ts": i.ts}
    watch = (session.query(LearnedBrand).order_by(LearnedBrand.last_seen.desc()).limit(12).all())
    picked = pick("video", lambda i: -(i.size or 0))
    vids = [row(i) for i in picked]
    try:
        from engine.comments import for_videos
        ids = [(i.key or "")[3:] for i in picked]
        cm = for_videos(session, ids)
        for v, vid in zip(vids, ids):
            v["comments"] = cm.get(vid)
    except Exception:
        pass
    return {"videos": vids,
            "ridicule": [row(i) for i in pick("ridicule", lambda i: -(i.size or 0))],
            "new_brands": [row(i) for i in pick("new brand", lambda i: -i.ts)],
            "watch": [{"name": b.shown or b.name, "hits": b.hits, "status": b.status, "example": b.example,
                       "last_seen": b.last_seen} for b in watch],
            "method": {"version": METHOD_VERSION, "since": method_start(session)}}
