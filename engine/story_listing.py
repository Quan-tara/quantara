"""
Story listing
=============
A story becomes LISTED when, in a collector run,
    story score >= LIST_SCORE   and   outlets >= LIST_MIN_OUTLETS.
It stays listed for LIST_WINDOW_H hours (72 h), then CLOSES with a final score —
the number grow/fade positions will settle on in the next step.

While listed the score is recomputed every run:
  • story still in the news  → score from this run's articles (story_score)
  • story dropped out        → same formula on the stored article list, so the
                               score fades with freshness instead of jumping to 0
Every run adds a point to the story's score history (narrative_points).
A story is listed at most once (same sid never relists).

Only outside news sources feed this. Nothing users do on the site is an input.
"""

import json
import time

import sqlalchemy as sa

from db.models import Narrative, NarrativePoint

LIST_SCORE       = 30.0
LIST_MIN_OUTLETS = 4
LIST_WINDOW_H    = 72
BACKFILL_H       = 72      # history copied from earlier readings when a story lists
MAX_ARTICLES     = 60


def _snapshot(s, old=None):
    """What we keep about a story so its page and its score survive after it leaves the news."""
    arts = {(a["title"], a["source"]): a for a in (old or {}).get("articles", [])}
    for a in s.get("members", []):
        arts[(a["title"], a["source"])] = a
    arts = sorted(arts.values(), key=lambda a: a["ts"], reverse=True)[:MAX_ARTICLES]
    outlets = sorted(set((old or {}).get("outlet_names", [])) | set(s.get("all_outlets") or []))
    return {
        "outlets": max(len(outlets), s.get("outlets", 0)),
        "outlet_names": outlets,
        "articles": arts,
        # current regions only (older readings may carry tags from before the outlet-based fix)
        "regions": sorted(s.get("regions") or (old or {}).get("regions", [])),
        "categories": sorted(set((old or {}).get("categories", [])) | set(s.get("categories") or [])),
        "severity": max(float((old or {}).get("severity", 0)), float(s.get("severity") or 0)),
        "tokens": list(s.get("tokens") or (old or {}).get("tokens", []))[:40],
        "hazards": list(s.get("hazards") or (old or {}).get("hazards", [])),
        "ts": max(float((old or {}).get("ts", 0)), float(s.get("ts") or 0)),
        "company": s.get("company") or (old or {}).get("company"),
        "sector": s.get("sector") or (old or {}).get("sector"),
        "trending": s.get("trending") or (old or {}).get("trending"),
        "outlets_eff": s.get("outlets_eff", (old or {}).get("outlets_eff")),
        "first_ts": min(x for x in [(old or {}).get("first_ts"), s.get("first_ts")] if x) if (s.get("first_ts") or (old or {}).get("first_ts")) else None,
    }


def _inputs_from_snapshot(snap, now):
    recent = sum(1 for a in snap.get("articles", []) if a["ts"] >= now - 12 * 3600)
    return {"outlets": snap.get("outlets", 0), "outlets_eff": snap.get("outlets_eff"),
            "regions": snap.get("regions", []), "severity": snap.get("severity", 0),
            "recent_12h": recent, "ts": snap.get("ts") or now}


def _inputs_from_live(s):
    return {k: s.get(k) for k in ("outlets", "outlets_eff", "regions", "severity", "recent_12h", "ts")}


def _score_from_snapshot(snap, now):
    """Score of a listed story that isn't in this run's news: same formula, stored articles."""
    from engine.narrative_index import story_score
    return story_score(_inputs_from_snapshot(snap, now), now)


# Per-arena listing rules. Brand scandals get far wider coverage than food recalls,
# so the bar is higher; DAILY_CAP stops a flood on a busy news day (highest scores first).
# "listing": False → the arena still updates and closes what it already listed, but lists
# nothing new. The game now lists only relatable everyday stories; food-safety recalls and
# formal brand scandals (court filings, fines) stay out of it. Food still feeds the FSNI.
ARENA_RULES = {
    "food":     {"score": LIST_SCORE, "outlets": LIST_MIN_OUTLETS, "daily_cap": None, "listing": False},
    "brands":   {"score": 40.0, "outlets": 6, "daily_cap": 5, "listing": False},
    "everyday": {"score": 30.0, "outlets": 4, "daily_cap": 5, "listing": True, "min_talk": 5},
}


def rules_for(arena):
    return ARENA_RULES.get(arena or "food", ARENA_RULES["food"])


def _arena_filter(q, arena):
    if (arena or "food") == "food":
        return q.filter(sa.or_(Narrative.arena == "food", Narrative.arena.is_(None)))
    return q.filter(Narrative.arena == arena)


def history_from_readings(session, sid, since_ts, arena="food"):
    """Score points for a story from the per-reading story_index (before it was listed)."""
    if (arena or "food") != "food":
        rows = session.execute(sa.text(
            "SELECT ts, story_index FROM arena_readings WHERE arena = :ar AND ts >= :a "
            "AND story_index LIKE :pat ORDER BY ts"
        ), {"ar": arena, "a": since_ts, "pat": f'%"sid": "{sid}"%'}).fetchall()
        out = []
        for ts, h in rows:
            try:
                for x in json.loads(h) or []:
                    if x.get("sid") == sid:
                        out.append((float(ts), float(x.get("score") or 0)))
                        break
            except Exception:
                continue
        return out
    rows = session.execute(sa.text(
        "SELECT ts, headlines FROM index_ticks WHERE ts >= :a AND headlines LIKE :pat ORDER BY ts"
    ), {"a": since_ts, "pat": f'%"sid": "{sid}"%'}).fetchall()
    out = []
    for ts, h in rows:
        try:
            for x in (json.loads(h) or {}).get("story_index") or []:
                if x.get("sid") == sid:
                    out.append((float(ts), float(x.get("score") or 0)))
                    break
        except Exception:
            continue
    return out


def _price_point(session, n, now):
    """Market price at this reading = base (score, or the line-bet chance in %) + crowd lean."""
    sid = n.sid
    try:
        from db.models import StoryMarket, StoryPricePoint
        from engine.story_market import grow_price, base_for
        m = session.query(StoryMarket).filter(StoryMarket.sid == sid).first()
        if m and m.status != "OPEN":
            return
        session.add(StoryPricePoint(sid=sid, ts=now, price=round(grow_price(base_for(n), m.crowd_offset if m else 0), 2)))
    except Exception as e:
        print(f"⚠️ price point error {sid}: {e}")


def _settle(session, sid, final=None, refund=False):
    try:
        from engine.story_market import settle
        k = settle(session, sid, final=final, refund=refund)
        if k:
            print(f"💰 {'Refunded' if refund else 'Settled'} {k} position(s) on story {sid}"
                  + ("" if refund else f" at final score {final:.1f}"))
    except Exception as e:
        print(f"⚠️ story settlement error {sid}: {e}")


def listed_for_matching(session, arena="food"):
    """LISTED stories of one arena in the shape assign_story_ids matches against."""
    out = []
    for n in _arena_filter(session.query(Narrative).filter(Narrative.status.in_(["LISTED", "DELISTED"])), arena).all():
        snap = json.loads(n.snapshot or "{}")
        out.append({"sid": n.sid, "first_seen": n.first_seen, "score": n.last_score,
                    "tokens": snap.get("tokens", []), "hazards": snap.get("hazards", []),
                    "company": snap.get("company")})
    return out


TALK_MIN_VIDEOS = 3


def talk_level(session, sid, company=None):
    """Are people actually talking about this story? (posts, videos, rising) from the latest
    social checks: Bluesky posts about it (24 h), YouTube videos about it (3 days), and whether
    the brand is spiking on Bluesky right now."""
    from db.models import SocialSignal
    posts = videos = None
    for r in (session.query(SocialSignal).filter(SocialSignal.sid == sid, SocialSignal.status == "OK",
                                                 SocialSignal.ts >= time.time() - 6 * 3600)
              .order_by(SocialSignal.ts).all()):
        if r.source == "youtube":
            videos = r.posts
        else:
            posts = r.posts
    rising = False
    if company:
        try:
            from engine.discovery import rising as _rising
            c = company.lower().replace("’", "'")
            rising = any(x["brand"] == c or x["brand"] in c for x in _rising(session))
        except Exception:
            pass
    return posts, videos, rising


def talked_about(session, sid, company, min_talk):
    posts, videos, rising = talk_level(session, sid, company)
    return rising or (posts or 0) >= min_talk or (videos or 0) >= TALK_MIN_VIDEOS


def update_listings(stories, session, now=None, arena="food"):
    """Call once per collector run of an arena, after assign_story_ids. Marks stories with
    listed / closes_at and keeps the narratives tables up to date. Only touches that arena."""
    now = now or time.time()
    arena = arena or "food"
    rules = rules_for(arena)
    live = {s["sid"]: s for s in stories if s.get("sid")}
    known = {n.sid: n for n in session.query(Narrative).filter(Narrative.sid.in_(list(live) or ["-"])).all()}
    active = _arena_filter(session.query(Narrative).filter(Narrative.status == "LISTED"), arena).all()
    for n in active:
        known.setdefault(n.sid, n)

    # 0) a listed story that is no longer separate in the news but has been absorbed by
    #    another listed story (same event, different words) → MERGED into that story
    from engine.narrative_index import same_event
    listed_live = [s for s in stories if s["sid"] in known and known[s["sid"]].status == "LISTED"]
    for n in list(active):
        if n.sid in live:
            continue
        snap = json.loads(n.snapshot or "{}")
        tk, hz = set(snap.get("tokens", [])), set(snap.get("hazards", []))
        mine = (snap.get("company") or "").lower()
        into = next((s for s in listed_live if s["sid"] != n.sid
                     and not (mine and (s.get("company") or "").lower() and mine != (s.get("company") or "").lower())
                     and same_event(tk, hz, set(s["tokens"]), set(s["hazards"]))), None)
        if into:
            snap["merged_into"] = into["sid"]
            n.snapshot = json.dumps(snap)
            n.status, n.closed_at = "MERGED", now
            n.final_score = n.last_score
            active.remove(n)
            _settle(session, n.sid, refund=True)
            print(f"🔗 Story merged  {n.sid} → {into['sid']}  {n.title[:60]}")

    # 1) update / close what is already listed
    for n in active:
        snap = json.loads(n.snapshot or "{}")
        s = live.get(n.sid)
        if s:
            snap = _snapshot(s, snap)
            score = s["story_score"]
            inputs = _inputs_from_live(s)
            n.title, n.url, n.source = s["title"], s["url"], s["source"]
            n.last_seen = now
        else:
            inputs = _inputs_from_snapshot(snap, now)
            score = _score_from_snapshot(snap, now)
        # exactly what this score was computed from — the story page's breakdown uses it,
        # and it stays frozen once the story closes
        snap["score_inputs"] = {**inputs, "at": now}
        n.snapshot = json.dumps(snap)
        n.last_score = score
        n.peak_score = max(n.peak_score or 0, score)
        session.add(NarrativePoint(sid=n.sid, ts=now, score=score))
        _price_point(session, n, now)
        if now >= n.closes_at:
            n.status, n.final_score, n.closed_at = "CLOSED", score, now
            _settle(session, n.sid, final=score)
            print(f"🔒 Story closed  {n.sid}  final {score:.1f}  {n.title[:70]}")

    # 2) list new stories that crossed the threshold (highest first, within the daily cap)
    room = None
    if not rules.get("listing", True):
        room = 0
    elif rules["daily_cap"]:
        # delisted stories (removed by a moderator) don't use up the day's places
        today = _arena_filter(session.query(Narrative).filter(Narrative.listed_at >= now - 86400,
                                                              Narrative.status != "DELISTED"), arena).count()
        room = max(0, rules["daily_cap"] - today)
    for s in sorted(stories, key=lambda x: x["story_score"], reverse=True):
        if s["sid"] in known:
            continue
        eff = s.get("outlets_eff", s["outlets"])
        if s["story_score"] < rules["score"] or eff < rules["outlets"]:
            continue
        # nobody talking about it → not listed, however many news sites wrote it up
        if rules.get("min_talk") and not talked_about(session, s["sid"], s.get("company"), rules["min_talk"]):
            continue
        if room is not None:
            if room <= 0:
                break
            room -= 1
        closes = now + LIST_WINDOW_H * 3600
        try:
            from engine.story_market import make_line
            bet = make_line(session, s, closes)
        except Exception as e:
            print(f"⚠️ line error {s['sid']}: {e}")
            bet = None
        snap0 = {**_snapshot(s), "score_inputs": {**_inputs_from_live(s), "at": now}}
        if bet:
            snap0["bet"] = bet
        n = Narrative(sid=s["sid"], title=s["title"], url=s["url"], source=s["source"], arena=arena,
                      status="LISTED", first_seen=s.get("first_seen"), listed_at=now,
                      closes_at=closes, list_score=s["story_score"],
                      last_score=s["story_score"], peak_score=s["story_score"],
                      last_seen=now, snapshot=json.dumps(snap0))
        session.add(n)
        for ts, sc in history_from_readings(session, s["sid"], now - BACKFILL_H * 3600, arena):
            session.add(NarrativePoint(sid=s["sid"], ts=ts, score=sc))
        session.add(NarrativePoint(sid=s["sid"], ts=now, score=s["story_score"]))
        _price_point(session, n, now)
        known[s["sid"]] = n
        print(f"📌 Story listed  {s['sid']}  score {s['story_score']:.1f}  "
              f"[{s['outlets']} outlets]  {s['title'][:70]}")

    session.commit()

    for s in stories:
        n = known.get(s["sid"])
        s["listed"] = bool(n and n.status == "LISTED")
        s["list_status"] = n.status if n else None
        s["closes_at"] = n.closes_at if n else None


def delist(sid, moderator_id, reason=None):
    """A moderator removes a listed story (wrong arena, politics, private person, roundup…).
    All positions are refunded. The story keeps its id, so it is never listed again."""
    from db.database import SessionLocal
    from engine.story_comments import is_moderator
    if not is_moderator(moderator_id):
        raise PermissionError("Moderators only")
    session = SessionLocal()
    try:
        n = session.query(Narrative).filter(Narrative.sid == sid).first()
        if not n:
            raise LookupError("Story not found")
        if n.status != "LISTED":
            raise ValueError(f"Story is already {n.status.lower()}")
        now = time.time()
        snap = json.loads(n.snapshot or "{}")
        snap["delisted"] = {"by": str(moderator_id), "reason": (reason or "")[:200], "at": now}
        n.snapshot = json.dumps(snap)
        n.status, n.closed_at = "DELISTED", now
        _settle(session, sid, refund=True)
        session.commit()
        print(f"🚫 Story delisted  {sid}  by moderator  {n.title[:70]}")
        return True
    finally:
        session.close()
