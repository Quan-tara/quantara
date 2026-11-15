"""
Story trading — GROW / FADE with virtual credits
================================================
Every LISTED story has a market. Two sides:
  GROW  unit pays  final score        credits at close
  FADE  unit pays  100 − final score  credits at close
(one GROW + one FADE always pay 100 together)

Price of a GROW unit = current story score + crowd offset   (kept within 1–99)
Price of a FADE unit = 100 − that.
  • With no trading, the price simply follows the story score.
  • Each trade moves the offset by IMPACT points per unit (GROW up, FADE down) and the
    offset stays — it is the crowd's lean: "this will end higher/lower than it is now".
  • The trader pays the average price over the move (half the impact).

Trading is open from listing until TRADING_STOP_H hours before the 72 h window closes.
Positions can be closed early at the current price (same impact, the other way).
At close: positions pay out on the final score. A story merged into another is refunded.

Nothing here feeds the story score or the FSNI — scores come only from the news.
"""

import time

from db.database import SessionLocal
from db.models import Narrative, StoryAccount, StoryMarket, StoryPosition, StoryPricePoint, StoryTrade

START_CREDITS     = 10000.0
IMPACT            = 0.10    # price points per unit traded
MAX_UNITS_TRADE   = 50
MAX_UNITS_STORY   = 100     # per user, per story, per side
TRADING_STOP_H    = 4       # trading stops this long before the window closes
EARLY_H           = 12      # units bought in the first 12 h of a listing are "early calls"
EARLY_BONUS       = 20.0    # an early unit that wins pays 120 instead of 100
PRICE_MIN, PRICE_MAX = 1.0, 99.0
OFFSET_LIMIT      = 40.0
SIDES = ("GROW", "FADE")


class TradeError(Exception):
    pass


def account(session, user_id, create=True):
    a = session.get(StoryAccount, user_id)
    if not a and create:
        a = StoryAccount(user_id=user_id, credits=START_CREDITS)
        session.add(a)
        session.flush()
    return a


def market(session, sid, lock=False):
    q = session.query(StoryMarket).filter(StoryMarket.sid == sid)
    if lock:
        q = q.with_for_update()
    m = q.first()
    if not m:
        m = StoryMarket(sid=sid, crowd_offset=0.0, status="OPEN", updated_at=time.time())
        session.add(m)
        session.flush()
        if lock:
            m = session.query(StoryMarket).filter(StoryMarket.sid == sid).with_for_update().first()
    return m


# ─────────────────────────────────────────────
# THE LINE BET (stories listed from this version on)
#   Each story gets a line at listing: the score it should end at if it fades normally.
#     JUST THE BEGINNING (side GROW) pays 100 per unit if the final score ends ABOVE the line
#     FORGOTTEN          (side FADE) pays 100 per unit if it ends AT or BELOW the line
#   Expected close = quiet projection (no new coverage) + K × (score now − quiet projection),
#   K learned from closed stories (default 0.2). At listing the line = expected close,
#   so both sides start near 50 %. The price is the model chance + the crowd's lean,
#   so the price reads as a percentage.
# Stories listed before keep the old linear GROW/FADE (pays the final score) until they close.
# ─────────────────────────────────────────────
import json as _json
import math as _math

LINE_K_DEFAULT = 0.20
LINE_K_MIN_SAMPLES = 8


def _phi(z):
    return 0.5 * (1 + _math.erf(z / _math.sqrt(2)))


def bet_of(n):
    try:
        return (_json.loads(n.snapshot or "{}") or {}).get("bet")
    except Exception:
        return None


def calibrate_k(session):
    """Share of a story's 'excess over a quiet fade' that typically survives to close,
    learned from closed line-bet stories (median). Default until enough data."""
    ks = []
    for m in session.query(Narrative).filter(Narrative.status == "CLOSED").all():
        b = bet_of(m)
        if not b or b.get("kind") != "line" or m.final_score is None:
            continue
        den = (m.list_score or 0) - b.get("quiet0", 0)
        if den > 3:
            ks.append((m.final_score - b["quiet0"]) / den)
    if len(ks) < LINE_K_MIN_SAMPLES:
        return LINE_K_DEFAULT, len(ks)
    ks.sort()
    return max(0.05, min(0.6, ks[len(ks) // 2])), len(ks)


def expected_close(n, now=None, k=None):
    """(expected final score, quiet projection) from the inputs of the last reading."""
    from engine.narrative_index import score_if_quiet
    snap = _json.loads(n.snapshot or "{}")
    si = dict(snap.get("score_inputs") or {})
    si.pop("at", None)
    b = snap.get("bet") or {}
    k = b.get("k", LINE_K_DEFAULT) if k is None else k
    if not si:
        return n.last_score or 0, n.last_score or 0
    arts = [a["ts"] for a in snap.get("articles", []) if a.get("ts")]
    quiet = score_if_quiet(si, n.closes_at, arts or None)
    cur = n.last_score or 0
    return quiet + k * max(0.0, cur - quiet), quiet


# ─────────────────────────────────────────────
# THE OUTLETS BET (food & drink stories listed from Nov 15 on) — a tangible question:
#   "Will at least <target> news outlets cover this by <close>?"
#     YES (side GROW) pays 100 per unit if the story reaches the target by the close
#     NO  (side FADE) pays 100 per unit if it doesn't
#   Outlets only ever go up, and they come from news sites (hard to fake with posts).
#   Target at listing = outlets now + the new outlets expected at the current pace
#   (at least 2), so both sides start near 50 %. Chance = Poisson chance of reaching the
#   target at the pace of the last 12 h, slowing as the story ages.
# ─────────────────────────────────────────────
PACE_DECAY = 0.6


def _snap(n):
    try:
        return _json.loads(n.snapshot or "{}") or {}
    except Exception:
        return {}


def outlets_now(n):
    s = _snap(n)
    return int(max(s.get("outlets") or 0, len(s.get("outlet_names") or [])))


def new_outlets(articles, since):
    """Outlets whose FIRST article came after `since`."""
    first = {}
    for a in articles or []:
        src, ts = (a.get("source") or "").strip().lower(), a.get("ts") or 0
        if src:
            first[src] = min(first.get(src, ts), ts)
    return sum(1 for t in first.values() if t >= since)


def _poisson_at_least(k, lam):
    if k <= 0:
        return 1.0
    p, term = 0.0, _math.exp(-lam)
    for i in range(k):
        p += term
        term *= lam / (i + 1)
    return max(0.0, 1.0 - p)


def outlets_chance(n, now=None):
    now = now or time.time()
    b = bet_of(n) or {}
    cur = outlets_now(n)
    if n.status != "LISTED":
        return 1.0 if b.get("final_outlets", cur) >= b["target"] else 0.0
    need = b["target"] - cur
    if need <= 0:
        return 0.99                                      # reached — it can't go back down
    pace = new_outlets(_snap(n).get("articles"), now - 12 * 3600) / 12.0     # outlets per hour
    hours = max(0.0, (n.closes_at - now) / 3600)
    return max(0.03, min(0.97, _poisson_at_least(need, pace * hours * PACE_DECAY)))


def make_outlets_bet(live_story, closes_at, now=None):
    now = now or time.time()
    cur = int(max(live_story.get("outlets") or 0, len(live_story.get("all_outlets") or [])))
    pace = new_outlets(live_story.get("members"), now - 12 * 3600) / 12.0
    expected = pace * max(0.0, (closes_at - now) / 3600) * PACE_DECAY
    target = cur + max(2, int(round(expected)))
    return {"kind": "outlets", "target": target, "start": cur, "pace12": round(pace * 12, 1)}


# ─────────────────────────────────────────────
# "WILL THEY CAVE?" (food & drink stories about a company, listed from Nov 15 on)
#   "Will <brand> back down within 7 days?" — apologise, reverse the change, bring the item back.
#     YES (side GROW) pays 100 per unit if a moderator confirms a cave before the deadline
#     NO  (side FADE) pays 100 per unit otherwise
#   The news scan flags headlines that look like a cave ("apologizes", "reverses", "brings
#   back"…) as a hint; only a moderator's confirmation settles YES (and closes it at once).
#   Model chance: 25 % (most storms pass without the brand backing down); 60 % once a hint
#   is flagged. The crowd moves the price from there.
# ─────────────────────────────────────────────
CAVE_BASE, CAVE_HINT = 0.25, 0.60
CAVE_DAYS = 7
import re as _re
CAVE_RX = _re.compile(r"\b(apologi[sz]es?|apologi[sz]ed|apology|reverses?|reversed|reversing|walks? back|walked back|"
                      r"backtracks?|backtracked|u-?turn|caves?|caved|backs? down|backed down|scraps?|scrapped|"
                      r"pulls? the plug|drops? (?:the )?(?:plan|change|price hike|new)|brings? (?:it )?back|bringing back|"
                      r"restores?|restored|reinstates?|reinstated|rolls? back|rolled back|reverts?|reverted|"
                      r"listened to (?:fans|customers))\b", _re.I)


def cave_hint(titles, company):
    """A headline that looks like the brand backing down, or None."""
    c = (company or "").lower().replace("’", "'")
    for t in titles:
        tl = (t or "").lower().replace("’", "'")
        if c and c in tl and CAVE_RX.search(tl):
            return t
    return None


def make_cave_bet(live_story, closes_at):
    return {"kind": "cave", "company": live_story.get("company"), "deadline": closes_at, "base": CAVE_BASE}


def line_chance(n, now=None):
    """Model chance (0–1) that the bet's YES side wins (line, outlets or cave bet)."""
    now = now or time.time()
    b = bet_of(n)
    if not b:
        return None
    if b.get("kind") == "cave":
        if b.get("caved"):
            return 1.0
        if n.status != "LISTED":
            return 0.0
        return CAVE_HINT if b.get("hint") else CAVE_BASE
    if b.get("kind") == "outlets":
        return outlets_chance(n, now)
    if n.status != "LISTED" and n.final_score is not None:
        return 1.0 if n.final_score > b["line"] else 0.0
    exp, _ = expected_close(n, now)
    window = max(1.0, n.closes_at - (n.listed_at or now))
    frac = max(0.0, min(1.0, (n.closes_at - now) / window))
    sigma = 2.0 + 8.0 * frac                    # less uncertain as the close approaches
    return max(0.03, min(0.97, _phi((exp - b["line"]) / sigma)))


def base_for(n):
    """Price before the crowd's lean: the score (old bet) or the model chance in % (line bet)."""
    c = line_chance(n)
    return (n.last_score or 0) if c is None else 100.0 * c


def grow_price(score, offset):
    return max(PRICE_MIN, min(PRICE_MAX, (score or 0) + (offset or 0)))


def trading_state(n, now=None):
    """(open?, reason, trading_stops_at)"""
    now = now or time.time()
    stops = n.closes_at - TRADING_STOP_H * 3600
    if n.status != "LISTED":
        return False, f"story is {n.status.lower()}", stops
    if now >= stops:
        return False, f"trading stopped {TRADING_STOP_H}h before close", stops
    return True, "", stops


def quote(score, offset, side, action, units):
    """(average credits per unit, new offset). No checks."""
    p0 = grow_price(score, offset)
    move = IMPACT * units
    up = (side == "GROW") == (action == "OPEN")          # buying GROW or closing FADE lifts the price
    p_mid = p0 + move / 2 if up else p0 - move / 2
    unit = p_mid if side == "GROW" else 100 - p_mid
    new_off = offset + (move if up else -move)
    return round(unit, 4), max(-OFFSET_LIMIT, min(OFFSET_LIMIT, new_off))


def close_value(score, offset, side, units):
    """What closing `units` now would pay (includes the price move of the close itself).
    Used as the honest value of an open position."""
    if not units:
        return 0.0
    unit, _ = quote(score, offset, side, "CLOSE", units)
    return max(0.0, unit * units)


def trade(user_id, sid, side, action, units):
    side, action = (side or "").upper(), (action or "").upper()
    if side not in SIDES or action not in ("OPEN", "CLOSE"):
        raise TradeError("side must be GROW or FADE, action OPEN or CLOSE")
    try:
        units = int(units)
    except Exception:
        raise TradeError("units must be a whole number")
    if units < 1 or units > MAX_UNITS_TRADE:
        raise TradeError(f"units must be 1–{MAX_UNITS_TRADE}")

    session = SessionLocal()
    try:
        now = time.time()
        n = session.query(Narrative).filter(Narrative.sid == sid).first()
        if not n:
            raise TradeError("story not listed")
        ok, why, _ = trading_state(n, now)
        if not ok:
            raise TradeError(f"Trading closed — {why}")
        m = market(session, sid, lock=True)
        if m.status != "OPEN":
            raise TradeError(f"Trading closed — market already {m.status.lower()}")
        acc = account(session, user_id)
        pos = (session.query(StoryPosition)
               .filter(StoryPosition.user_id == user_id, StoryPosition.sid == sid,
                       StoryPosition.status == "OPEN").with_for_update().all())
        mine = {p.side: p for p in pos}
        other = mine.get("FADE" if side == "GROW" else "GROW")

        score = n.last_score or 0
        base = base_for(n)
        unit, new_off = quote(base, m.crowd_offset, side, action, units)
        p_after = grow_price(base, new_off)
        if p_after <= PRICE_MIN or p_after >= PRICE_MAX:
            raise TradeError("price limit reached — try fewer units")
        credits = round(unit * units, 2)

        if action == "OPEN":
            if other and other.units > 0:
                raise TradeError(f"you hold {other.side} on this story — close it first")
            p = mine.get(side)
            held = p.units if p else 0
            if held + units > MAX_UNITS_STORY:
                raise TradeError(f"max {MAX_UNITS_STORY} units per story (you hold {held:g})")
            if acc.credits < credits:
                raise TradeError(f"not enough credits ({acc.credits:.0f} available, {credits:.0f} needed)")
            if not p:
                p = StoryPosition(user_id=user_id, sid=sid, side=side, units=0, cost=0,
                                  realized=0, status="OPEN", opened_at=now)
                session.add(p)
            p.units += units
            p.cost = round(p.cost + credits, 4)
            acc.credits = round(acc.credits - credits, 4)
        else:
            p = mine.get(side)
            if not p or p.units < units:
                raise TradeError(f"you hold {p.units if p else 0:g} {side} units")
            avg = p.cost / p.units
            p.realized = round(p.realized + credits - avg * units, 4)
            p.cost = round(p.cost - avg * units, 4)
            p.units -= units
            acc.credits = round(acc.credits + credits, 4)
            if p.units <= 0:
                p.units, p.cost, p.status, p.payout, p.settled_at = 0, 0, "CLOSED", 0, now

        m.crowd_offset, m.updated_at = new_off, now
        session.add(StoryTrade(user_id=user_id, sid=sid, side=side, action=action, units=units,
                               price=unit, credits=credits, score=score, ts=now))
        session.add(StoryPricePoint(sid=sid, ts=now, price=round(p_after, 2)))
        session.commit()
        return {"side": side, "action": action, "units": units, "price": round(unit, 2),
                "credits": credits, "balance": round(acc.credits, 2),
                "new_price": round(p_after, 2)}
    except TradeError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def settle(session, sid, final=None, refund=False):
    """Pay out every open position on a story. Called by the listing engine when a
    story closes (final score) or is merged into another (refund)."""
    m = market(session, sid)
    if m.status != "OPEN":
        return 0
    now = time.time()
    nar = session.query(Narrative).filter(Narrative.sid == sid).first()
    bet = bet_of(nar) if nar else None
    n = 0
    if bet and bet.get("kind") == "outlets" and not refund and nar:
        bet["final_outlets"] = outlets_now(nar)               # frozen for the page
        snap = _snap(nar)
        snap["bet"] = bet
        nar.snapshot = _json.dumps(snap)
    for p in session.query(StoryPosition).filter(StoryPosition.sid == sid,
                                                 StoryPosition.status == "OPEN").all():
        if refund:
            pay, p.status = p.cost, "REFUNDED"
        elif bet:                                   # line / outlets bet: all or nothing
            if bet.get("kind") == "cave":
                yes = bool(bet.get("caved"))
            elif bet.get("kind") == "outlets":
                yes = bet["final_outlets"] >= bet["target"]
            else:
                yes = final > bet["line"]
            won = (p.side == "GROW") == yes
            per = 100.0 if won else 0.0
            pay = per * p.units
            if won and nar and nar.listed_at:
                # early call: units bought in the first EARLY_H hours pay a bonus
                early = sum(t.units if t.action == "OPEN" else -t.units for t in
                            session.query(StoryTrade).filter(StoryTrade.sid == sid, StoryTrade.user_id == p.user_id,
                                                             StoryTrade.side == p.side,
                                                             StoryTrade.ts <= nar.listed_at + EARLY_H * 3600).all())
                pay += EARLY_BONUS * max(0.0, min(p.units, early))
            pay, p.status = round(pay, 4), "SETTLED"
        else:
            per = final if p.side == "GROW" else 100 - final
            pay, p.status = round(per * p.units, 4), "SETTLED"
        account(session, p.user_id).credits += pay
        p.payout, p.settled_at = round(pay, 4), now
        n += 1
    m.status = "REFUNDED" if refund else "SETTLED"
    m.final = None if refund else final
    m.updated_at = now
    return n


def market_view(session, n, user_id=None):
    """Everything the story page needs about its market."""
    now = time.time()
    m = session.query(StoryMarket).filter(StoryMarket.sid == n.sid).first()
    off = m.crowd_offset if m else 0.0
    score = n.last_score or 0
    ok, why, stops = trading_state(n, now)
    base = base_for(n)
    p = grow_price(base, off)
    held = {"GROW": 0, "FADE": 0}
    traders = set()
    for q in session.query(StoryPosition).filter(StoryPosition.sid == n.sid).all():
        traders.add(q.user_id)
        if q.status == "OPEN":
            held[q.side] += q.units
    trades = (session.query(StoryTrade).filter(StoryTrade.sid == n.sid)
              .order_by(StoryTrade.ts.desc()).limit(25).all())
    out = {
        "status": m.status if m else "OPEN", "open": ok, "closed_reason": why,
        "trading_stops_at": stops, "price": round(p, 2), "fade_price": round(100 - p, 2),
        "offset": round(off, 2), "score": round(score, 2), "final": m.final if m else None,
        "impact": IMPACT, "max_units": MAX_UNITS_TRADE, "max_units_story": MAX_UNITS_STORY,
        "held": held, "traders": len(traders),
        "bet": _bet_view(n, now),
        "trades": [{"user_id": str(t.user_id), "side": t.side, "action": t.action, "units": t.units,
                    "price": round(t.price, 2), "ts": t.ts} for t in trades],
    }
    if user_id:
        acc = account(session, user_id)
        session.commit()
        mine = []
        for q in session.query(StoryPosition).filter(StoryPosition.sid == n.sid,
                                                     StoryPosition.user_id == user_id).all():
            if q.status == "CLOSED" and not q.realized:
                continue
            mark = close_value(base, off, q.side, q.units) if q.status == "OPEN" else (q.payout or 0)
            mine.append({"side": q.side, "units": q.units, "cost": round(q.cost, 2),
                         "avg": round(q.cost / q.units, 2) if q.units else None,
                         "status": q.status, "payout": q.payout, "realized": round(q.realized, 2),
                         "value": round(mark, 2),
                         "pnl": round(mark - q.cost + q.realized, 2)})
        out["me"] = {"credits": round(acc.credits, 2), "positions": mine}
    return out


def _bet_view(n, now):
    b = bet_of(n)
    if not b:
        return {"kind": "linear"}
    early_until = (n.listed_at or now) + EARLY_H * 3600
    if b.get("kind") == "cave":
        return {"kind": "cave", "company": b.get("company"), "deadline": b.get("deadline", n.closes_at),
                "closes_at": n.closes_at, "hint": b.get("hint"), "caved": b.get("caved"),
                "chance": round(100 * (line_chance(n, now) or 0), 1),
                "early_until": early_until, "early_bonus": EARLY_BONUS}
    if b.get("kind") == "outlets":
        arts = _snap(n).get("articles")
        return {"kind": "outlets", "target": b["target"], "start": b.get("start"), "now": outlets_now(n),
                "final_outlets": b.get("final_outlets"), "new_24h": new_outlets(arts, now - 86400),
                "chance": round(100 * (outlets_chance(n, now) or 0), 1), "closes_at": n.closes_at,
                "early_until": early_until, "early_bonus": EARLY_BONUS}
    exp, quiet = expected_close(n, now)
    return {"kind": "line", "line": round(b["line"], 1), "expected": round(exp, 1),
            "quiet": round(quiet, 1), "chance": round(100 * (line_chance(n, now) or 0), 1),
            "k": b.get("k"), "closes_at": n.closes_at, "early_until": early_until, "early_bonus": EARLY_BONUS}


def make_line(session, live_story, closes_at):
    """Line for a story being listed now (expected close under a normal fade)."""
    from engine.narrative_index import score_if_quiet
    k, samples = calibrate_k(session)
    si = {k_: live_story.get(k_) for k_ in ("outlets", "outlets_eff", "regions", "severity", "recent_12h", "ts")}
    arts = [a["ts"] for a in live_story.get("members", []) if a.get("ts")]
    quiet = score_if_quiet(si, closes_at, arts or None)
    cur = live_story["story_score"]
    line = quiet + k * max(0.0, cur - quiet)
    return {"kind": "line", "line": round(line, 2), "k": round(k, 3), "k_samples": samples,
            "quiet0": round(quiet, 2), "score0": cur}
