"""
Sector stocks — the main game
=============================
Five food sectors, each with a live stock price that FALLS when drama rises:
    fast food & restaurants · snacks & drinks · groceries & prices · food safety · diets & food trends

HEAT (0–100) per sector, every collector run (15 min):
  food safety  = the FSNI (recalls, outbreaks, FDA) — the safety half of the index
  the others   = average of the 3 strongest food & drink stories in that sector (empty slots
                 count 0), each weighted by damage potential and talk, as in the index
PRICE = 100 − 2 × (heat − normal), kept between 5 and 200
  normal = the sector's median heat over the last 7 days (20 while it warms up)
  → 100 is a normal day; 80 = clearly more drama than usual; 120 = calmer than usual

Players trade with the same virtual credits as the story game:
  BUY  = you expect the sector to stay calm or calm down (LONG)
  SELL = you expect trouble (SHORT) — or you sell shares you hold
  LONG worth  = shares × price          SHORT worth = shares × (2 × entry − price), at least 0
  A 0.5% spread each way. No expiry: hold as long as you like.

The price comes only from the index — trading never moves it (no one can push a price).
What the crowd DOES tells us: the share of shares SOLD in the last 3 days is the crowd's
forecast ("trouble ahead") that feeds the company page's outlook.
"""

import json
import statistics
import time

from db.models import SectorPosition, SectorTick, SectorTrade, StoryAccount

SECTOR_LIST = ["fast food & restaurants", "snacks & drinks", "groceries & prices", "food safety", "diets & food trends"]
LABELS = {"fast food & restaurants": "Fast food", "snacks & drinks": "Snacks & drinks",
          "groceries & prices": "Groceries", "food safety": "Food safety", "diets & food trends": "Diets & trends"}
TOP_N = 3
NORMAL_DEFAULT = 20.0
SLOPE = 2.0
PRICE_MIN, PRICE_MAX = 5.0, 200.0
SPREAD = 0.005
MAX_SHARES_TRADE = 200
MOOD_DAYS = 3
DAY = 86400


class SectorError(Exception):
    pass


def price_for(heat, normal):
    return round(max(PRICE_MIN, min(PRICE_MAX, 100.0 - SLOPE * (heat - (normal if normal is not None else NORMAL_DEFAULT)))), 2)


def _normal(session, sector, now, heat):
    """Median heat of the last 7 days. While a sector warms up (under 20 readings) the current
    reading is included, so a new sector starts at a price of about 100 instead of jumping."""
    rows = [r.heat for r in session.query(SectorTick.heat).filter(SectorTick.sector == sector,
                                                                   SectorTick.ts >= now - 7 * DAY).all()]
    if len(rows) < 20:
        rows = rows + [heat]
    return round(statistics.median(rows), 2)


def compute_sectors(session, stories, safety):
    """Store one tick per sector. `stories` = the index's weighted story rows."""
    now = time.time()
    out = {}
    for sec in SECTOR_LIST:
        mine = sorted([s for s in stories if s.get("sector") == sec], key=lambda s: s.get("weighted") or 0, reverse=True)
        if sec == "food safety" and safety is not None:
            heat = float(safety)
        else:
            heat = sum((s.get("weighted") or 0) for s in mine[:TOP_N]) / TOP_N
        normal = _normal(session, sec, now, heat)
        p = price_for(heat, normal)
        top = [{"sid": s["sid"], "title": s.get("title"), "weighted": s.get("weighted"), "company": s.get("company")}
               for s in mine[:3]]
        session.add(SectorTick(ts=now, sector=sec, heat=round(heat, 2), normal=normal, price=p, top=json.dumps(top)))
        out[sec] = p
    session.commit()
    return out


def latest(session):
    """{sector: SectorTick} — newest reading of each sector."""
    out = {}
    for t in session.query(SectorTick).filter(SectorTick.ts >= time.time() - 2 * DAY).order_by(SectorTick.ts).all():
        out[t.sector] = t
    return out


def _account(session, user_id):
    a = session.get(StoryAccount, user_id)
    if not a:
        a = StoryAccount(user_id=user_id, credits=10000.0)
        session.add(a)
        session.flush()
    return a


def _value(p, price):
    if p.shares <= 0:
        return 0.0
    entry = p.cost / p.shares
    per = price if p.side == "LONG" else max(0.0, 2 * entry - price)
    return p.shares * per


def trade(session, user_id, sector, action, shares):
    """BUY or SELL `shares` of a sector at the current price (± spread).
    BUY covers a SHORT first, then goes LONG; SELL sells a LONG first, then goes SHORT."""
    action = (action or "").upper()
    if sector not in SECTOR_LIST:
        raise SectorError("unknown sector")
    if action not in ("BUY", "SELL"):
        raise SectorError("action must be BUY or SELL")
    try:
        shares = float(int(shares))
    except Exception:
        raise SectorError("shares must be a whole number")
    if shares < 1 or shares > MAX_SHARES_TRADE:
        raise SectorError(f"shares must be 1–{MAX_SHARES_TRADE}")
    t = latest(session).get(sector)
    if not t:
        raise SectorError("no price yet — try again after the next reading")
    now = time.time()
    px = round(t.price * (1 + SPREAD if action == "BUY" else 1 - SPREAD), 4)
    acc = _account(session, user_id)
    pos = (session.query(SectorPosition).filter(SectorPosition.user_id == user_id, SectorPosition.sector == sector,
                                                SectorPosition.status == "OPEN").with_for_update().first())
    left, credits_in, credits_out, filled = shares, 0.0, 0.0, 0.0
    closing = "SHORT" if action == "BUY" else "LONG"
    opening = "LONG" if action == "BUY" else "SHORT"
    if pos and pos.side == closing and pos.shares > 0:
        k = min(left, pos.shares)
        entry = pos.cost / pos.shares
        per = px if closing == "LONG" else max(0.0, 2 * entry - px)
        got = per * k
        pos.realized = round(pos.realized + got - entry * k, 4)
        pos.cost = round(pos.cost - entry * k, 4)
        pos.shares -= k
        acc.credits = round(acc.credits + got, 4)
        credits_in += got
        filled += k
        left -= k
        if pos.shares <= 0:
            pos.shares, pos.cost, pos.status = 0.0, 0.0, "CLOSED"
            pos = None
    if left > 0:
        need = px * left
        if acc.credits < need:
            if credits_in:
                left = 0          # the closing part is done; not enough credits to open the rest
            else:
                raise SectorError(f"not enough credits ({acc.credits:.0f} available, {need:.0f} needed)")
        else:
            if not pos:
                pos = SectorPosition(user_id=user_id, sector=sector, side=opening, shares=0.0, cost=0.0,
                                     realized=0.0, status="OPEN", opened_at=now)
                session.add(pos)
            pos.shares += left
            pos.cost = round(pos.cost + need, 4)
            acc.credits = round(acc.credits - need, 4)
            credits_out += need
            filled += left
    done = filled
    session.add(SectorTrade(user_id=user_id, sector=sector, action=action, shares=done, price=px,
                            credits=round(credits_in + credits_out, 2), ts=now))
    session.commit()
    return {"sector": sector, "action": action, "shares": done, "price": round(px, 2),
            "received": round(credits_in, 2), "paid": round(credits_out, 2), "balance": round(acc.credits, 2)}


def mood(session, sector=None, days=MOOD_DAYS):
    """Share (0–100) of shares SOLD in the last days — the crowd's 'trouble ahead' signal."""
    q = session.query(SectorTrade).filter(SectorTrade.ts >= time.time() - days * DAY)
    if sector:
        q = q.filter(SectorTrade.sector == sector)
    rows = q.all()
    tot = sum(r.shares for r in rows)
    if not tot:
        return None, 0
    sold = sum(r.shares for r in rows if r.action == "SELL")
    return round(100 * sold / tot, 1), len({r.user_id for r in rows})


def positions(session, user_id):
    lat = latest(session)
    out = []
    for p in session.query(SectorPosition).filter(SectorPosition.user_id == user_id,
                                                  SectorPosition.status == "OPEN").all():
        t = lat.get(p.sector)
        val = _value(p, t.price) if t else p.cost
        out.append({"sector": p.sector, "label": LABELS.get(p.sector, p.sector), "side": p.side,
                    "shares": p.shares, "entry": round(p.cost / p.shares, 2) if p.shares else None,
                    "cost": round(p.cost, 2), "value": round(val, 2), "pnl": round(val - p.cost + p.realized, 2)})
    return out


def profit_by_user(session):
    """{user_id: profit} over all sector trading (open positions at today's price)."""
    lat = latest(session)
    out = {}
    for p in session.query(SectorPosition).all():
        t = lat.get(p.sector)
        val = _value(p, t.price) if (t and p.status == "OPEN") else 0.0
        out[p.user_id] = out.get(p.user_id, 0.0) + (val - p.cost if p.status == "OPEN" else 0.0) + p.realized
    return {k: round(v, 2) for k, v in out.items()}


def board(session, user_id=None):
    """Everything the sector stocks section needs."""
    now = time.time()
    lat = latest(session)
    hist = {}
    for t in (session.query(SectorTick.sector, SectorTick.ts, SectorTick.price, SectorTick.heat)
              .filter(SectorTick.ts >= now - 7 * DAY).order_by(SectorTick.ts).all()):
        hist.setdefault(t.sector, []).append([t.ts, t.price, t.heat])
    out = []
    for sec in SECTOR_LIST:
        t = lat.get(sec)
        h = hist.get(sec, [])
        step = max(1, len(h) // 120)
        ago = next((p[1] for p in reversed(h) if p[0] <= now - DAY), None)
        m, traders = mood(session, sec)
        out.append({"sector": sec, "label": LABELS[sec], "price": t.price if t else None, "heat": t.heat if t else None,
                    "normal": t.normal if t else None, "ts": t.ts if t else None,
                    "change_24h": None if (t is None or ago is None) else round(t.price - ago, 2),
                    "spark": h[::step] + ([h[-1]] if h and (len(h) - 1) % step else []),
                    "drivers": json.loads(t.top or "[]") if t else [], "sell_pct": m, "traders": traders})
    res = {"sectors": out, "spread": SPREAD, "max_shares": MAX_SHARES_TRADE}
    if user_id is not None:
        acc = _account(session, user_id)
        session.commit()
        res["me"] = {"credits": round(acc.credits, 2), "positions": positions(session, user_id)}
    return res
