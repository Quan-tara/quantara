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
from db.models import Narrative, StoryAccount, StoryMarket, StoryPosition, StoryTrade

START_CREDITS     = 10000.0
IMPACT            = 0.10    # price points per unit traded
MAX_UNITS_TRADE   = 50
MAX_UNITS_STORY   = 100     # per user, per story, per side
TRADING_STOP_H    = 6       # trading stops this long before the window closes
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
        acc = account(session, user_id)
        pos = (session.query(StoryPosition)
               .filter(StoryPosition.user_id == user_id, StoryPosition.sid == sid,
                       StoryPosition.status == "OPEN").with_for_update().all())
        mine = {p.side: p for p in pos}
        other = mine.get("FADE" if side == "GROW" else "GROW")

        score = n.last_score or 0
        unit, new_off = quote(score, m.crowd_offset, side, action, units)
        p_after = grow_price(score, new_off)
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
    n = 0
    for p in session.query(StoryPosition).filter(StoryPosition.sid == sid,
                                                 StoryPosition.status == "OPEN").all():
        if refund:
            pay, p.status = p.cost, "REFUNDED"
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
    p = grow_price(score, off)
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
            mark = close_value(score, off, q.side, q.units) if q.status == "OPEN" else (q.payout or 0)
            mine.append({"side": q.side, "units": q.units, "cost": round(q.cost, 2),
                         "avg": round(q.cost / q.units, 2) if q.units else None,
                         "status": q.status, "payout": q.payout, "realized": round(q.realized, 2),
                         "value": round(mark, 2),
                         "pnl": round(mark - q.cost + q.realized, 2)})
        out["me"] = {"credits": round(acc.credits, 2), "positions": mine}
    return out
