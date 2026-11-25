"""
Social-first stories (24 Nov 2026)
==================================
Reputation fires usually start on social media and reach the news later, if at all. Until now a story
could only be listed once 3+ news outlets wrote about it, so the board stayed empty on days when the
real drama was on Bluesky (24 Nov: Chick-fil-A at 47× its normal volume, Nestlé 24×, Starbucks 8× —
a boycott list going round — and no listing).

A food brand that SURGES on Bluesky with real complaints is now listed too, with the same question:
"Will it tip?" — here: does it reach the news? Rule:
  listed   the brand is rising on Bluesky (discovery: a spike against its own normal level) at least
           SOCIAL_X × normal, with at least SOCIAL_COMPLAINTS posts that complain (boycott, rip-off, sick…),
           no other story about the brand listed in the last 7 days, within the board's daily cap.
  tipped   within the rule's days, news coverage of the brand's trouble reaches the rule's outlet target
           (it is already "beyond the news" by definition: it started there). Matching news stories about
           the brand are absorbed into it each run, so its outlet count grows as the press picks it up.
  faded    the days run out first.
"""

import hashlib
import json
import time

SOCIAL_X = 5.0
SOCIAL_COMPLAINTS = 10
_SHOWN = {"chick-fil-a": "Chick-fil-A", "mcdonald's": "McDonald's", "nestlé": "Nestlé", "nestle": "Nestlé",
          "kfc": "KFC", "ihop": "IHOP", "a&w": "A&W", "7-eleven": "7-Eleven", "in-n-out": "In-N-Out",
          "papa john's": "Papa John's", "dunkin'": "Dunkin'", "bj's wholesale": "BJ's Wholesale"}


def shown(brand):
    b = (brand or "").lower()
    return _SHOWN.get(b) or " ".join(w[:1].upper() + w[1:] for w in b.split())


def candidates(session):
    """Brands surging on Bluesky with real complaints right now, strongest first."""
    from engine.discovery import rising
    out = [r for r in rising(session) if (r.get("x") or 0) >= SOCIAL_X and (r.get("complaints") or 0) >= SOCIAL_COMPLAINTS]
    return sorted(out, key=lambda r: -(r["complaints"] or 0))


_OTHER_NAMES = ["hobby lobby", "target", "amazon", "walmart", "tesla", "meta", "home depot", "goya", "arby's",
                "hardee's", "pilgrim's", "chick-fil-a", "nestlé", "nestle", "starbucks", "mcdonald's", "coca-cola",
                "pepsi", "hershey", "mars", "papa john's", "trader joe's", "kroger", "costco", "shein", "zara", "paramount", "hbo"]


def _others(brand, text, words):
    """Other brand names in the top post / top words — 2+ of them means a boycott LIST, not a scandal."""
    from engine import narrative_index as ni
    from engine.everyday import FOOD_BRANDS
    low = text.lower().replace("’", "'")          # the post itself: side words come from many different posts
    me = (brand or "").lower()
    seen = []
    for n in _OTHER_NAMES + list(FOOD_BRANDS):
        if n != me and not (me in n or n in me) and ni._has_any(low, [n]) and shown(n) not in seen:
            if not any(n in x.lower() or x.lower() in n for x in seen):
                seen.append(shown(n))
    return seen


def _title(c):
    """(full title, short title). Never 'people slam hobby lobby target' — a post naming many brands is a list."""
    tp = c.get("top_post") or {}
    text = (tp.get("text") if isinstance(tp, dict) else str(tp or "")) or ""
    words = c.get("words") or []
    boycott = "boycott" in text.lower() or "boycott" in " ".join(words).lower()
    b = shown(c["brand"])
    stats = f"({c['x']}× normal, {c['complaints']} complaining posts in 3 h)"
    others = _others(c["brand"], text, words)
    if len(others) >= 2:
        return (f"{b} named in viral boycott lists on Bluesky, alongside {', '.join(others[:3])} {stats}",
                f"{b} named in viral boycott lists online")
    what = "boycott calls surge" if boycott else "complaints surge"
    return (f"{b}: {what} on Bluesky {stats}", f"{b} {'boycott calls' if boycott else 'complaints'} surge online")


def _sid(brand, now):
    return "bs" + hashlib.md5(f"{brand}|{int(now // 86400)}".encode()).hexdigest()[:10]


def recently_listed(session, brand, now, arena="everyday"):
    """Any story about this brand listed in the last 7 days (news or social)?"""
    from db.models import Narrative
    from engine.story_listing import _same_brand
    for o in session.query(Narrative).filter(Narrative.listed_at >= now - 7 * 86400, Narrative.arena == arena).all():
        snap = json.loads(o.snapshot or "{}")
        if _same_brand(snap.get("lead_brand") or snap.get("company"), brand):
            return True
    return False


def list_new(session, now, room, arena="everyday"):
    """List social-first stories; returns how many places were used."""
    from db.models import Narrative, NarrativePoint
    from engine.tipping import rule as tip_rule
    used = 0
    for c in candidates(session):
        if room is not None and used >= room:
            break
        if recently_listed(session, c["brand"], now, arena):
            continue
        sid = _sid(c["brand"], now)
        if session.query(Narrative).filter(Narrative.sid == sid).first():
            continue
        rl = tip_rule(session)
        title, short = _title(c)
        tp = c.get("top_post") or {}
        url = (tp.get("url") if isinstance(tp, dict) else None) or f"https://bsky.app/search?q={c['brand'].replace(' ', '+')}"
        social = {"posts": c["posts"], "x": c["x"], "complaints": c["complaints"], "words": c.get("words") or [],
                  "top_post": tp if isinstance(tp, dict) else None, "at": c["ts"]}
        bet = {"kind": "tip", "company": shown(c["brand"]), "start": 0, "target": int(rl["outlets"]),
               "rule": {"outlets": rl["outlets"], "growth": rl["growth"], "days": rl["days"], "rate": rl.get("rate"),
                        "provisional": rl["provisional"]},
               "talk": True, "tipped": False, "social": social}
        snap = {"articles": [], "outlet_names": [], "outlets": 0, "regions": ["US"], "categories": [], "hazards": [],
                "tokens": [], "company": shown(c["brand"]), "lead_brand": shown(c["brand"]), "origin": "bluesky",
                "social": social, "listed_title": title, "short_title": short, "bet": bet, "ts": now,
                "title_by": "rule", "title_v": 999}        # written by the rule above; the AI title writer leaves it
        n = Narrative(sid=sid, title=title, url=url, source="Bluesky", arena=arena, status="LISTED",
                      first_seen=c["ts"], listed_at=now, closes_at=now + rl["days"] * 86400, list_score=0.0,
                      last_score=0.0, peak_score=0.0, last_seen=now, snapshot=json.dumps(snap))
        session.add(n)
        session.add(NarrativePoint(sid=sid, ts=now, score=0.0))
        try:
            from engine.reality import log_listed
            log_listed(session, sid, title, shown(c["brand"]))
        except Exception:
            pass
        used += 1
        print(f"📌 Story listed  {sid}  from Bluesky  {title[:90]}")
    return used


def ni_boycott(s):
    t = " ".join([s.get("title") or ""] + [m.get("title") or "" for m in s.get("members", [])]).lower()
    return "boycott" in t


def is_list(snap):
    """A 'named in boycott lists' story: general chatter, not one specific scandal. It takes in only news on
    its own topic and never blocks a different news story about the same brand."""
    return "boycott lists" in (snap.get("short_title") or snap.get("listed_title") or "")


def absorb(n, stories):
    """A social-first story picks up the news coverage of its brand's trouble: articles from this run's
    stories about the same brand that pass the tip filter are merged in. Returns True if it changed."""
    from engine.story_listing import _lead_brand, _same_brand
    from engine.tipping import tip_gate
    snap = json.loads(n.snapshot or "{}")
    if snap.get("origin") != "bluesky":
        return False
    brand = snap.get("lead_brand") or snap.get("company")
    retitled = False
    if snap.get("title_by") != "rule" and snap.get("social"):      # stories listed before 25 Nov: clearer titles
        so = snap["social"]
        title, short = _title({"brand": brand, "x": so.get("x"), "complaints": so.get("complaints"),
                               "words": so.get("words") or [], "top_post": so.get("top_post")})
        n.title, snap["listed_title"], snap["short_title"] = title, title, short
        snap["title_by"], snap["title_v"] = "rule", 999
        retitled = True
    have = {(a.get("title"), a.get("source")) for a in snap.get("articles", [])}
    added = 0
    for s in stories:
        lead = _lead_brand(s.get("title")) or s.get("company")
        if not lead or not _same_brand(lead, brand) or not tip_gate(s.get("title"), [], lead)[0]:
            continue
        if is_list(snap) and not ni_boycott(s):
            continue
        for m in s.get("members", []):
            k = (m.get("title"), m.get("source"))
            if k in have:
                continue
            have.add(k)
            snap.setdefault("articles", []).append({x: m.get(x) for x in ("title", "source", "url", "ts", "region")})
            added += 1
    if not added:
        if retitled:
            n.snapshot = json.dumps(snap)
        return retitled
    snap["outlet_names"] = sorted({a["source"] for a in snap["articles"] if a.get("source")})
    snap["outlets"] = len(snap["outlet_names"])
    n.snapshot = json.dumps(snap)
    print(f"📰 Story {n.sid} ({brand}): {added} news article(s) picked up — {snap['outlets']} outlets now")
    return True
