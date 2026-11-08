"""
Food Reputation Index (FRI) — the one number companies buy protection on
========================================================================
  FRI = 50% SAFETY  (the FSNI: recalls, outbreaks, FDA, GDELT — unchanged)
      + 50% SOCIAL  (food & drink stories people argue about)

SOCIAL = average of the 5 strongest food & drink stories (story score 0–100, empty slots
count 0). A story people are really talking about counts more: up to +50% for Bluesky
posts about it (40 posts in 24 h = the full +50%).

OUTLOOK (0–100) — where the index is likely heading, from four early signals:
  crowd     35%  players' bets: average chance they give "just the beginning" on listed stories
  buzz      25%  food brands spiking on Bluesky right now (each brand = 25)
  searches  20%  share of checked stories where people search "boycott / cancel / alternative"
  momentum  20%  how fast the index rose over the last 24 h (+10 points = 100)
Parts without data are left out and the others re-weighted.
  under 35 = calm · 35–60 = watch · over 60 = pressure building

PROTECTION (demo) — pays if the FRI AVERAGES above the strike over the cover period:
  payout = cover × min(100%, 10% per point above the strike)
Premium = expected payout × 1.25 (+ a 1% floor). The expected payout comes from how the
index has moved in the past (FSNI history until the FRI has its own), shifted up when
the outlook says pressure is building.

Back-test (Nov 2026, 16 past cases): the signal rose on or before the event day; most of the
share-price damage came 1–3 months later → cover periods of 1–3 months.
"""

import json
import math
import random
import statistics
import time

from db.database import SessionLocal
from db.models import (ArenaReading, BehaviourSignal, IndexTick, Narrative, ProtectionPosition,
                       ReputationTick, SocialSignal)

SAFETY_W, SOCIAL_W = 0.5, 0.5
TOP_N = 5
TALK_FULL = 40
WEIGHTS = {"crowd": 0.35, "buzz": 0.25, "searches": 0.20, "momentum": 0.20}
PAY_PER_POINT = 0.10
LOADING, FLOOR = 1.25, 0.01
DAY = 86400


# ─────────────────────────────────────────────
# INDEX
# ─────────────────────────────────────────────
def _latest_fsni(session):
    t = (session.query(IndexTick).filter(IndexTick.headlines.isnot(None))
         .order_by(IndexTick.ts.desc()).first())
    return t.value if t else None


def _talk(session, sids):
    out = {}
    for r in (session.query(SocialSignal).filter(SocialSignal.sid.in_(list(sids) or ["-"]),
                                                 SocialSignal.source == "bluesky", SocialSignal.status == "OK",
                                                 SocialSignal.ts >= time.time() - DAY)
              .order_by(SocialSignal.ts).all()):
        out[r.sid] = r.posts or 0
    return out


def _social(session):
    r = (session.query(ArenaReading).filter(ArenaReading.arena == "everyday")
         .order_by(ArenaReading.ts.desc()).first())
    if not r or r.ts < time.time() - 2 * 3600:
        return None, []
    stories = json.loads(r.story_index or "[]")
    talk = _talk(session, [s["sid"] for s in stories])
    rows = []
    for s in stories:
        posts = talk.get(s["sid"], 0)
        w = (s.get("score") or 0) * (1 + 0.5 * min(1.0, posts / TALK_FULL))
        rows.append({"sid": s["sid"], "title": s["title"], "score": round(s.get("score") or 0, 1),
                     "weighted": round(min(100.0, w), 1), "posts": posts, "company": s.get("company"),
                     "sector": s.get("sector"), "listed": bool(s.get("listed")), "url": s.get("url"),
                     "source": s.get("source")})
    rows.sort(key=lambda x: x["weighted"], reverse=True)
    top = rows[:TOP_N]
    social = sum(x["weighted"] for x in top) / TOP_N
    return round(social, 2), rows[:15]


# ─────────────────────────────────────────────
# OUTLOOK
# ─────────────────────────────────────────────
def _crowd(session):
    from engine.story_market import market_view
    rows = []
    for n in session.query(Narrative).filter(Narrative.status == "LISTED").all():
        if (n.arena or "food") != "everyday":
            continue
        try:
            v = market_view(session, n)
        except Exception:
            continue
        rows.append((v["price"], v["traders"]))
    if not rows:
        return None, {"stories": 0, "players": 0}
    w = sum(1 + t for _, t in rows)
    grow = sum(p * (1 + t) for p, t in rows) / w
    return max(0.0, min(100.0, (grow - 50) * 2 + 50)), {"stories": len(rows), "players": sum(t for _, t in rows),
                                                        "grow_chance": round(grow, 1)}


def _buzz(session):
    try:
        from engine.discovery import rising
        r = rising(session)
    except Exception:
        return None, {}
    return min(100.0, 25.0 * len(r)), {"rising": [x["brand"] for x in r][:6]}


def _searches(session):
    rows = (session.query(BehaviourSignal).filter(BehaviourSignal.ts >= time.time() - 2 * DAY,
                                                  BehaviourSignal.status == "OK").all())
    if not rows:
        return None, {"checked": 0}
    acted = sum(1 for r in rows if json.loads(r.result or "{}").get("acted"))
    return 100.0 * acted / len(rows), {"checked": len(rows), "acted": acted}


def _momentum(session, value):
    old = (session.query(ReputationTick).filter(ReputationTick.ts <= time.time() - DAY)
           .order_by(ReputationTick.ts.desc()).first())
    if not old:
        return None, {}
    d = value - old.value
    return max(0.0, min(100.0, d * 10)), {"change_24h": round(d, 2)}


def label_for(o):
    return None if o is None else "calm" if o < 35 else "watch" if o <= 60 else "pressure building"


def outlook(session, value):
    parts, used = {}, {}
    for name, fn in (("crowd", lambda: _crowd(session)), ("buzz", lambda: _buzz(session)),
                     ("searches", lambda: _searches(session)), ("momentum", lambda: _momentum(session, value))):
        try:
            score, info = fn()
        except Exception as e:
            score, info = None, {"error": str(e)[:80]}
        parts[name] = {"score": None if score is None else round(score, 1), "weight": WEIGHTS[name], **info}
        if score is not None:
            used[name] = score
    if not used:
        return None, parts
    tw = sum(WEIGHTS[k] for k in used)
    return round(sum(WEIGHTS[k] * v for k, v in used.items()) / tw, 1), parts


# ─────────────────────────────────────────────
# READING (called every collector run)
# ─────────────────────────────────────────────
def compute_reputation():
    session = SessionLocal()
    try:
        safety = _latest_fsni(session)
        safety = None if safety is None else round(safety, 2)
        social, top = _social(session)
        if safety is None and social is None:
            return
        value = round(SAFETY_W * (safety or 0) + SOCIAL_W * (social or 0), 2) if safety is not None and social is not None \
            else round(safety if social is None else social, 2)
        hist = [r.value for r in session.query(ReputationTick).filter(ReputationTick.ts >= time.time() - 7 * DAY).all()]
        normal = round(statistics.median(hist), 2) if len(hist) >= 20 else None
        o, parts = outlook(session, value)
        session.add(ReputationTick(ts=time.time(), value=value, safety=safety, social=social, normal=normal,
                                   outlook=o, label=label_for(o), parts=json.dumps(parts), top=json.dumps(top)))
        session.commit()
        settle_due(session)
        print(f"🧭 Food Reputation Index {value:.1f} (safety {safety if safety is not None else '—'} · social "
              f"{social if social is not None else '—'}) | outlook {o if o is not None else '—'} {label_for(o) or ''}")
    finally:
        session.close()


def snapshot(session, days=7):
    rows = (session.query(ReputationTick).filter(ReputationTick.ts >= time.time() - days * DAY)
            .order_by(ReputationTick.ts).all())
    if not rows:
        return {"value": None, "series": [], "message": "no reading yet — the first comes with the next collector run"}
    last = rows[-1]
    ago = lambda h: next((r.value for r in reversed(rows) if r.ts <= last.ts - h * 3600), None)
    d24, d7 = ago(24), (rows[0].value if rows[0].ts <= last.ts - 6 * DAY else None)
    step = max(1, len(rows) // 300)
    return {"ts": last.ts, "value": last.value, "safety": last.safety, "social": last.social,
            "normal": last.normal, "change_24h": None if d24 is None else round(last.value - d24, 2),
            "change_7d": None if d7 is None else round(last.value - d7, 2),
            "outlook": last.outlook, "label": last.label, "parts": json.loads(last.parts or "{}"),
            "top": json.loads(last.top or "[]"),
            "weights": {"safety": SAFETY_W, "social": SOCIAL_W, "outlook": WEIGHTS},
            "series": [[r.ts, r.value, r.safety, r.social] for r in rows[::step]] + ([] if (len(rows) - 1) % step == 0
                                                                                     else [[last.ts, last.value, last.safety, last.social]])}


def for_company(session, sector=None, brand=None):
    """Live stories that touch the company's category or name it."""
    snap = snapshot(session, days=1)
    b = (brand or "").strip().lower().replace("’", "'")
    out = []
    for s in snap.get("top") or []:
        hit_brand = bool(b) and b in ((s.get("company") or "") + " " + s.get("title", "")).lower().replace("’", "'")
        hit_sector = bool(sector) and s.get("sector") == sector
        if hit_brand or hit_sector:
            out.append({**s, "match": "your brand" if hit_brand else "your category"})
    out.sort(key=lambda x: (x["match"] != "your brand", -x["weighted"]))
    return out


# ─────────────────────────────────────────────
# PROTECTION — pricing, buying, settling
# ─────────────────────────────────────────────
def _daily_means(rows):
    days = {}
    for ts, v in rows:
        days.setdefault(int(ts // DAY), []).append(v)
    return [sum(v) / len(v) for _, v in sorted(days.items())]


def _future_shifts(session, days):
    """How the average over the next `days` differed from the level at the start, historically.
    Uses the FRI's own history once it has 2×days of it, else the FSNI's."""
    fri = [(r.ts, r.value) for r in session.query(ReputationTick.ts, ReputationTick.value).all()]
    src, series = "FRI", _daily_means(fri)
    if len(series) < 2 * days + 5:
        fs = [(r.ts, r.value) for r in session.query(IndexTick.ts, IndexTick.value)
              .filter(IndexTick.headlines.isnot(None)).all()]
        src, series = "FSNI", _daily_means(fs)
    shifts = [sum(series[i + 1:i + 1 + days]) / days - series[i] for i in range(len(series) - days - 1)]
    if len(shifts) >= 10:
        return shifts, f"{src} history ({len(series)} days)"
    sd = 3.0 * math.sqrt(days / 7)                     # provisional until there is enough history
    rnd = random.Random(42)
    return [rnd.gauss(0, sd) for _ in range(2000)], "provisional (not enough history yet)"


def quote(session, days=30, strike=None, cover=10000.0):
    snap = snapshot(session, days=1)
    level = snap.get("value")
    if level is None:
        return {"error": "no index reading yet"}
    normal = snap.get("normal") or level
    strike = float(strike) if strike is not None else round(normal + 10)
    shifts, basis = _future_shifts(session, days)
    sd = statistics.pstdev(shifts) or 1.0
    o = snap.get("outlook")
    tilt = 0.0 if o is None else (o - 50) / 50 * 0.5 * sd    # pressure building → higher expected average
    pays, hit = [], 0
    for s in shifts:
        avg = level + s + tilt
        f = max(0.0, min(1.0, (avg - strike) * PAY_PER_POINT))
        pays.append(f)
        hit += avg > strike
    exp = sum(pays) / len(pays)
    premium = cover * (exp * LOADING + FLOOR)
    return {"days": days, "strike": strike, "cover": cover, "level": level, "normal": snap.get("normal"),
            "outlook": o, "label": snap.get("label"),
            "chance_pays": round(100 * hit / len(shifts), 1), "expected_payout": round(cover * exp, 2),
            "premium": round(premium, 2), "premium_pct": round(100 * premium / cover, 2),
            "full_payout_at": strike + 1 / PAY_PER_POINT, "basis": basis,
            "terms": f"Pays if the Food Reputation Index averages above {strike:g} over the next {days} days: "
                     f"{int(PAY_PER_POINT * 100)}% of the cover per point above, full cover at "
                     f"{strike + 1 / PAY_PER_POINT:g} or more."}


def buy(session, user_id, days, strike, cover, sector=None, brand=None):
    q = quote(session, days, strike, cover)
    if q.get("error"):
        return q
    now = time.time()
    p = ProtectionPosition(user_id=user_id, sector=sector, brand=(brand or None) and brand[:60], notional=cover,
                           strike=q["strike"], start_ts=now, end_ts=now + days * DAY, premium=q["premium"])
    session.add(p)
    session.commit()
    return {"id": p.id, **q}


def average_between(session, a, b):
    vals = [r.value for r in session.query(ReputationTick.value)
            .filter(ReputationTick.ts >= a, ReputationTick.ts <= b).all()]
    return sum(vals) / len(vals) if vals else None


def settle_due(session):
    now = time.time()
    for p in session.query(ProtectionPosition).filter(ProtectionPosition.status == "OPEN",
                                                      ProtectionPosition.end_ts <= now).all():
        avg = average_between(session, p.start_ts, p.end_ts)
        if avg is None:
            continue
        p.average = round(avg, 2)
        p.payout = round(p.notional * max(0.0, min(1.0, (avg - p.strike) * PAY_PER_POINT)), 2)
        p.status, p.settled_at = "SETTLED", now
        print(f"🛡️ protection #{p.id} settled: average {avg:.1f} vs strike {p.strike:g} → payout {p.payout:,.0f}")
    session.commit()


def positions(session, user_id):
    out = []
    for p in (session.query(ProtectionPosition).filter(ProtectionPosition.user_id == user_id)
              .order_by(ProtectionPosition.start_ts.desc()).all()):
        live = p.average if p.status == "SETTLED" else average_between(session, p.start_ts, min(time.time(), p.end_ts))
        would = None if live is None else round(p.notional * max(0.0, min(1.0, (live - p.strike) * PAY_PER_POINT)), 2)
        out.append({"id": p.id, "sector": p.sector, "brand": p.brand, "cover": p.notional, "strike": p.strike,
                    "start": p.start_ts, "end": p.end_ts, "premium": p.premium, "status": p.status,
                    "average_so_far": None if live is None else round(live, 2),
                    "payout": p.payout if p.status == "SETTLED" else would})
    return out
