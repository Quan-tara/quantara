"""
SPOT IT FIRST — the first stage of a story card.

  spot it  → a player says "trouble is brewing at <brand>" (+ reason, + link) before the news has it
  call it  → once the news confirms it, the story is listed with "Will they cave?"
  prove it → evidence comments, "called it"

Rules
  • one shared card per brand (Narrative, status SPOTTED); every spotter's reason is an evidence
    comment on it (YES = trouble is coming, NO = nothing will happen)
  • max MAX_OPEN open spots per player, one per brand
  • refused if the brand is already in the news (a story about it counts in the index or is listed)
  • WON automatically when, within SPOT_DAYS, a story about the brand passes the gate
    (US, a real demand, real stakes) — the same referee as the index and the listing
  • payout: SPOT_BASE + SPOT_PER_DAY for every full day early, + FIRST_BONUS for the first spotter
  • nothing is lost if it doesn't happen — the card just expires
"""
import hashlib
import json
import time

from db.models import Narrative, Spot

SPOT_DAYS    = 14
MAX_OPEN     = 3
SPOT_BASE    = 100.0
SPOT_PER_DAY = 20.0
FIRST_BONUS  = 50.0
MIN_REASON   = 15
DAY = 86400
CARD_STATES = ("SPOTTED", "CONFIRMED", "EXPIRED")


class SpotError(Exception):
    pass


def _key(name):
    return (name or "").lower().replace("’", "'").strip()


def us_brands():
    """Brands players can spot: the US food & drink brands we follow, shown in their usual spelling."""
    from engine.everyday import FOOD_BRANDS, GROCERS, NON_US_BRANDS
    out, seen = [], set()
    for b in FOOD_BRANDS + GROCERS:
        k = _key(b)
        if k in seen or k in NON_US_BRANDS or k in ("mcdonalds", "dominos", "coke", "pret", "costa", "leon"):
            continue
        seen.add(k)
        out.append(k)
    return out


def display(key):
    special = {"mcdonald's": "McDonald's", "kfc": "KFC", "ihop": "IHOP", "m&m's": "M&M's", "h-e-b": "H-E-B",
               "in-n-out": "In-N-Out", "chick-fil-a": "Chick-fil-A", "7-eleven": "7-Eleven"}
    if key in special:
        return special[key]
    return " ".join(w[:1].upper() + w[1:] for w in key.split(" "))


def real_trouble(title, key, company=None):
    """Confirms a spot only if the story is real trouble for THIS brand (28 Nov 2026: a soup review confirmed
    "trouble at Costco"): the brand is named in the headline itself, the headline passes the index's pressure
    test AND the "Will it tip?" trouble test."""
    from engine.everyday import brand_in, index_gate
    from engine.tipping import tip_gate
    if not title or _key(brand_in([title])) != key:
        return False
    return index_gate(title, [], company)[0] and tip_gate(title, [], company)[0]


def undo(session, card_sid, now=None, why="moderator"):
    """Reverse a wrong confirmation: take the payouts back, reopen the spots (or expire them if too old),
    put the card back to SPOTTED and clear the called evidence."""
    from engine.story_market import account
    from db.models import StoryComment
    now = now or time.time()
    n = session.query(Narrative).filter(Narrative.sid == card_sid).first()
    won = session.query(Spot).filter(Spot.card_sid == card_sid, Spot.status == "WON").all()
    if not won:
        return 0
    for s in won:
        acc = account(session, s.user_id)
        acc.credits = (acc.credits or 0) - (s.payout or 0)
        s.status = "OPEN" if now - s.ts <= SPOT_DAYS * DAY else "EXPIRED"
        s.story_sid = s.story_title = s.lead_s = s.payout = None
        s.resolved_at = None if s.status == "OPEN" else now
    for c in session.query(StoryComment).filter(StoryComment.sid == card_sid).all():
        if c.bonus:
            acc = account(session, c.user_id)
            acc.credits = (acc.credits or 0) - c.bonus
            c.bonus = None
        c.called, c.lead_s = None, None
    if n:
        snap = json.loads(n.snapshot or "{}")
        snap.pop("confirmed_by", None)
        snap.setdefault("undone", []).append({"at": now, "why": why})
        n.snapshot = json.dumps(snap)
        still = any(s.status == "OPEN" for s in won)
        n.status, n.closed_at = ("SPOTTED", None) if still else ("EXPIRED", now)
    print(f"↩️ Spot card {card_sid}: confirmation undone ({why}), {len(won)} payout(s) taken back")
    return len(won)


def _undo_wrong(session, now):
    """Confirmations made under the old, looser rule that the new rule would not make are undone once."""
    done = 0
    for card in {s.card_sid for s in session.query(Spot).filter(Spot.status == "WON").all()}:
        ws = session.query(Spot).filter(Spot.card_sid == card, Spot.status == "WON").first()
        if ws and not real_trouble(ws.story_title, ws.brand):
            done += undo(session, card, now, why="not real trouble for the brand (stricter rule, 28 Nov 2026)")
    return done


def _brand_of(title, company=None):
    from engine.everyday import brand_in
    return _key(company) or _key(brand_in([title or ""]))


def in_the_news(session, key):
    """A story about this brand already counts in the index, or is listed → (sid, title), else None."""
    from engine.reputation import snapshot
    for r in (snapshot(session, days=1).get("top") or []):
        if (r.get("weighted") or 0) > 0 and _brand_of(r.get("title"), r.get("company")) == key:
            return r.get("sid"), r.get("title")
    for n in session.query(Narrative).filter(Narrative.status == "LISTED").all():
        snap = json.loads(n.snapshot or "{}")
        if _key(snap.get("lead_brand") or snap.get("company")) == key or _brand_of(n.title) == key:
            return n.sid, n.title
    return None


def card_for(session, key, now):
    """The open SPOTTED card of a brand (created if needed)."""
    for n in session.query(Narrative).filter(Narrative.status == "SPOTTED").all():
        if (json.loads(n.snapshot or "{}").get("spot") or {}).get("key") == key:
            return n, False
    name = display(key)
    sid = "sp" + hashlib.sha1(f"{key}|{now}".encode()).hexdigest()[:10]
    n = Narrative(sid=sid, title=f"Trouble brewing at {name}?", url=None, source="players", status="SPOTTED",
                  first_seen=now, listed_at=now, closes_at=now + SPOT_DAYS * DAY, list_score=0.0,
                  last_score=0.0, peak_score=0.0, last_seen=now, arena="everyday",
                  snapshot=json.dumps({"spot": {"key": key, "brand": name}, "lead_brand": name, "company": name}))
    session.add(n)
    session.flush()
    return n, True


def _link_in(text):
    from engine.story_comments import URL_RX
    m = URL_RX.search(text or "")
    return m.group(0) if m else None


def create(session, user_id, brand, reason, link=None):
    """A player spots trouble at a brand. Returns the card sid."""
    from engine.story_comments import post, CommentError
    key = _key(brand)
    if key not in us_brands():
        raise SpotError("Pick a brand from the list (US food & drink brands)")
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON:
        raise SpotError("Say what's brewing, in a sentence or two")
    from engine.spot_check import check
    ok, why = check(reason, (link or "").strip() or _link_in(reason), display(key))
    if not ok:
        raise SpotError(why)
    now = time.time()
    opened = session.query(Spot).filter(Spot.user_id == user_id, Spot.status == "OPEN").all()
    if any(s.brand == key for s in opened):
        raise SpotError(f"You already spotted {display(key)} — add to the discussion on its card instead")
    if len(opened) >= MAX_OPEN:
        raise SpotError(f"You have {MAX_OPEN} open spots — wait until one is decided")
    news = in_the_news(session, key)
    if news:
        raise SpotError(f"{display(key)} is already in the news. Bet on that story instead.|{news[0]}")
    n, fresh = card_for(session, key, now)
    first = fresh or not session.query(Spot).filter(Spot.card_sid == n.sid).count()
    session.commit()                      # the card must exist before the reason is posted on it
    try:
        cid = post(user_id, n.sid, reason, kind="YES", link=link)
    except CommentError as e:
        if fresh:
            session.delete(n)
            session.commit()
        raise SpotError(str(e))
    session.add(Spot(user_id=user_id, brand=key, card_sid=n.sid, comment_id=cid, ts=now, status="OPEN", first=first))
    from engine.story_comments import is_moderator
    if fresh and not is_moderator(user_id):          # test spots by moderators never enter the track record
        try:
            from db.models import ForwardLog
            session.add(ForwardLog(ts=now, kind="spot", brand=key, sid=n.sid, title=n.title))
        except Exception:
            pass
    session.commit()
    return n.sid


def _pay(session, s, now, story_sid, story_title):
    from engine.story_market import account
    lead = max(0.0, now - s.ts)
    pay = SPOT_BASE + SPOT_PER_DAY * int(lead // DAY) + (FIRST_BONUS if s.first else 0.0)
    s.status, s.story_sid, s.story_title, s.lead_s, s.payout, s.resolved_at = "WON", story_sid, (story_title or "")[:300], lead, pay, now
    account(session, s.user_id).credits += pay


def resolve(session, rows, now=None):
    """Called every reading with the index rows. Confirms spots whose brand now has a story that
    passes the gate (weighted > 0); expires spots older than SPOT_DAYS."""
    from engine.story_comments import call_it
    now = now or time.time()
    try:
        if _undo_wrong(session, now):
            session.flush()
    except Exception as e:
        print(f"⚠️ spot undo check: {e}")
    open_spots = session.query(Spot).filter(Spot.status == "OPEN").all()
    if not open_spots:
        session.commit()
        return 0
    hit = {}
    for r in rows or []:
        if (r.get("weighted") or 0) > 0:
            k = _brand_of(r.get("title"), r.get("company"))
            if k and k not in hit and real_trouble(r.get("title"), k, r.get("company")):
                hit[k] = (r.get("sid"), r.get("title"))
    done = 0
    cards = {}
    for s in open_spots:
        if s.brand in hit:
            _pay(session, s, now, *hit[s.brand])
            cards[s.card_sid] = ("CONFIRMED", hit[s.brand])
            done += 1
        elif now - s.ts > SPOT_DAYS * DAY:
            s.status, s.resolved_at = "EXPIRED", now
            cards.setdefault(s.card_sid, ("EXPIRED", None))
            done += 1
    session.flush()                                   # so the checks below see the new spot states
    for sid, (state, story) in cards.items():
        n = session.query(Narrative).filter(Narrative.sid == sid).first()
        if not n or n.status != "SPOTTED":
            continue
        if state == "EXPIRED" and session.query(Spot).filter(Spot.card_sid == sid, Spot.status == "OPEN").count():
            continue                                  # newer spotters on the same card are still waiting
        n.status, n.closed_at = state, now
        snap = json.loads(n.snapshot or "{}")
        if story:
            snap["confirmed_by"] = {"sid": story[0], "title": story[1], "at": now}
        n.snapshot = json.dumps(snap)
        try:
            call_it(session, sid, "YES" if state == "CONFIRMED" else "NO", now)
        except Exception as e:
            print(f"⚠️ spot called-it error {sid}: {e}")
        print(f"🔍 Spot card {sid} {state.lower()}: {n.title}" + (f" → {story[1][:60]}" if story else ""))
    session.commit()
    return done


def view_card(session, n, viewer_id=None):
    snap = json.loads(n.snapshot or "{}")
    rows = session.query(Spot).filter(Spot.card_sid == n.sid).order_by(Spot.ts).all()
    from engine.story_comments import is_moderator
    return {"sid": n.sid, "status": n.status, "title": n.title, "brand": (snap.get("spot") or {}).get("brand"),
            "key": (snap.get("spot") or {}).get("key"), "opened": n.listed_at, "closes": n.closes_at,
            "spotters": len(rows), "first_ts": rows[0].ts if rows else n.listed_at,
            "confirmed_by": snap.get("confirmed_by"),
            "won": [{"lead_s": s.lead_s, "payout": s.payout, "first": s.first} for s in rows if s.status == "WON"],
            "mine": next(({"status": s.status, "payout": s.payout, "lead_s": s.lead_s} for s in rows
                          if viewer_id is not None and s.user_id == viewer_id), None),
            "moderator": bool(viewer_id is not None and is_moderator(viewer_id))}


def board(session, viewer_id=None):
    """Open cards plus the ones decided in the last 3 days, and the viewer's spots."""
    now = time.time()
    cards = (session.query(Narrative).filter(Narrative.status.in_(CARD_STATES),
                                             (Narrative.status == "SPOTTED") | (Narrative.closed_at >= now - 3 * DAY))
             .order_by(Narrative.listed_at.desc()).all())
    mine = []
    if viewer_id is not None:
        for s in session.query(Spot).filter(Spot.user_id == viewer_id).order_by(Spot.ts.desc()).limit(30).all():
            mine.append({"brand": display(s.brand), "card_sid": s.card_sid, "ts": s.ts, "status": s.status,
                         "payout": s.payout, "lead_s": s.lead_s, "story_sid": s.story_sid, "story_title": s.story_title,
                         "expires": s.ts + SPOT_DAYS * DAY})
    return {"cards": [view_card(session, n, viewer_id) for n in cards], "mine": mine,
            "open": sum(1 for m in mine if m["status"] == "OPEN"),
            "rules": {"days": SPOT_DAYS, "max_open": MAX_OPEN, "base": SPOT_BASE, "per_day": SPOT_PER_DAY,
                      "first_bonus": FIRST_BONUS, "min_reason": MIN_REASON},
            "brands": [{"key": k, "name": display(k)} for k in sorted(us_brands())]}


def for_brand(session, name, days=SPOT_DAYS):
    """Company page: how many players spotted trouble at this brand recently."""
    key = _key(name)
    rows = session.query(Spot).filter(Spot.brand == key, Spot.ts >= time.time() - days * DAY).all()
    return {"spotters": len(rows), "open": sum(1 for s in rows if s.status == "OPEN"),
            "card_sid": rows[-1].card_sid if rows else None}
