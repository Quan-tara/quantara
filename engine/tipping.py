"""
Does a story TIP? (22 Nov 2026 — the backbone model, see "Quantara — The Backbone Model")
==========================================================================================
Every brand story is in one of four states:
  SPARK      in the news, but small or not growing
  SPREADING  3+ outlets and 2+ NEW outlets in the last 12 h, about a named brand, something at stake
             (index_gate) → it is listed for players with one question: "Will this story tip?"
  TIPPED     within TIP_DAYS of becoming Spreading it
               1. reaches the rule's mainstream size (rule.outlets outlets),
               2. and grew by at least rule.growth outlets since it was listed,
               3. and is seen beyond the news: 5+ Bluesky posts about it, 6+ YouTube videos about it,
                  or the brand spiking on Bluesky.
  FADED      the days run out without that.

The mainstream size is calibrated ONCE on stored history: of the candidates, the one at which about
a quarter of spreading stories tipped (TARGET_RATE) — a real question, neither always-no nor always-yes.
While the history has fewer than FREEZE_CASES finished cases, the rule is provisional and recalibrates
daily; then it is frozen. Every bet copies its target at listing, so open bets never change.
"""

import json
import threading
import time

SPREAD_MIN_OUTLETS = 3
SPREAD_NEW_12H = 2
TIP_GROWTH = 4
TIP_DAYS = 5
DEFAULT_OUTLETS = 10
CANDIDATES = (6, 8, 10, 12, 15, 20)
TARGET_RATE = 0.25
MIN_CASES = 10
FREEZE_CASES = 30
TALK_POSTS, TALK_VIDEOS = 5, 6
PACE_TAU_H = 24          # news pace fades: a story's next outlets come mostly within about a day
_cal = {"running": False, "ts": 0.0}


def _norm(b):
    return (b or "").lower().replace("’", "'").strip()


# ─────────────────────────────── the rule ───────────────────────────────
def rule(session):
    from db.models import TipRule
    r = session.query(TipRule).order_by(TipRule.ts.desc()).first()
    if not r:
        return {"outlets": DEFAULT_OUTLETS, "growth": TIP_GROWTH, "days": TIP_DAYS, "rate": None,
                "cases": 0, "provisional": True, "calibrated": False}
    return {"outlets": r.outlets, "growth": r.growth, "days": r.days, "rate": r.rate, "cases": r.cases,
            "provisional": bool(r.provisional), "calibrated": True, "ts": r.ts,
            "report": json.loads(r.report or "{}")}


def target_for(start_outlets, rl):
    return max(int(rl["outlets"]), int(start_outlets) + int(rl["growth"]))


def is_spreading(s, now=None):
    """A live story (collector shape) that has just started spreading — the listing condition."""
    from engine.everyday import index_gate
    from engine.story_market import new_outlets
    now = now or time.time()
    cur = int(max(s.get("outlets") or 0, len(s.get("all_outlets") or [])))
    if cur < SPREAD_MIN_OUTLETS:
        return False
    if new_outlets(s.get("members"), now - 12 * 3600) < SPREAD_NEW_12H:
        return False
    return bool(index_gate(s.get("title"), [m.get("title") for m in s.get("members", [])], s.get("company"))[0])


def beyond_news(session, sid, company, since):
    """Seen beyond the news since `since`: Bluesky posts or YouTube videos about the story, or the brand
    spiking on Bluesky."""
    from db.models import BrandBuzz, SocialSignal
    for src, posts in (session.query(SocialSignal.source, SocialSignal.posts)
                       .filter(SocialSignal.sid == sid, SocialSignal.status == "OK", SocialSignal.ts >= since).all()):
        if (posts or 0) >= (TALK_VIDEOS if src == "youtube" else TALK_POSTS):
            return True
    c = _norm(company)
    if c:
        q = session.query(BrandBuzz.brand).filter(BrandBuzz.spike.is_(True), BrandBuzz.ts >= since).all()
        if any(_norm(b) == c or (_norm(b) and _norm(b) in c) for (b,) in q):
            return True
    return False


def make_bet(s, closes_at, rl):
    cur = int(max(s.get("outlets") or 0, len(s.get("all_outlets") or [])))
    return {"kind": "tip", "company": s.get("company"), "start": cur, "target": target_for(cur, rl),
            "rule": {"outlets": rl["outlets"], "growth": rl["growth"], "days": rl["days"], "rate": rl.get("rate"),
                     "provisional": rl["provisional"]}, "talk": False, "tipped": False}


def chance(n, now=None):
    """Model chance that a listed story tips before its close: half the Poisson chance of the missing outlets
    (news pace fades: expected new outlets = pace of the last 12 h × PACE_TAU_H), half the share of spreading
    stories that tipped in the history; lower while nobody beyond the news talks about it. The crowd moves
    the price from there."""
    from engine.story_market import _poisson_at_least, _snap, new_outlets, outlets_now
    now = now or time.time()
    b = (_snap(n) or {}).get("bet") or {}
    if b.get("tipped"):
        return 1.0
    if n.status != "LISTED":
        return 0.0
    need = b["target"] - outlets_now(n)
    pace = new_outlets(_snap(n).get("articles"), now - 12 * 3600) / 12.0
    hours = max(0.0, (n.closes_at - now) / 3600)
    p_out = 1.0 if need <= 0 else _poisson_at_least(need, pace * min(hours, PACE_TAU_H))
    base = (b.get("rule") or {}).get("rate") or TARGET_RATE
    p = 0.5 * p_out + 0.5 * base if hours > 0 else p_out
    p *= 1.0 if b.get("talk") else 0.8
    return max(0.03, min(0.97, p))


def state_of(n):
    b = ((json.loads(n.snapshot or "{}") or {}).get("bet") or {})
    if b.get("kind") != "tip":
        return None
    if b.get("tipped"):
        return "TIPPED"
    return "SPREADING" if n.status == "LISTED" else "FADED"


# ─────────────────────────────── calibration on stored history ───────────────────────────────
def replay(session, arena="everyday"):
    """Every story that became Spreading in the stored readings, with what followed within TIP_DAYS:
    [{sid, title, company, ts, start, max_outlets, beyond}] — finished windows only."""
    from db.models import ArenaReading, BrandBuzz, SocialSignal
    from engine.everyday import brand_in, index_gate
    hist, started, latest, last_ts = {}, {}, None, 0.0
    q = (session.query(ArenaReading.ts, ArenaReading.story_index).filter(ArenaReading.arena == arena)
         .order_by(ArenaReading.ts))
    for ts, idx in q.yield_per(200):
        if ts - last_ts < 3 * 3600:
            continue
        last_ts = latest = ts
        try:
            stories = json.loads(idx or "[]")
        except Exception:
            continue
        for s in stories:
            sid, o = s.get("sid"), int(s.get("outlets") or 0)
            if not sid:
                continue
            h = hist.setdefault(sid, [])
            h.append((ts, o))
            if sid in started:
                st = started[sid]
                if ts <= st["ts"] + TIP_DAYS * 86400:
                    st["max_outlets"] = max(st["max_outlets"], o)
                continue
            before = [x for t, x in h if t <= ts - 12 * 3600]
            new12 = o - (before[-1] if before else 0)
            company = s.get("company") or brand_in([s.get("title") or ""])
            if o >= SPREAD_MIN_OUTLETS and new12 >= SPREAD_NEW_12H and company \
                    and index_gate(s.get("title"), [], company)[0]:
                started[sid] = {"sid": sid, "title": (s.get("title") or "")[:160], "company": company,
                                "ts": ts, "start": o, "max_outlets": o}
    if latest is None:
        return [], None
    done = [c for c in started.values() if c["ts"] + TIP_DAYS * 86400 <= latest]
    social = {}
    for sid, src, ts, posts in session.query(SocialSignal.sid, SocialSignal.source, SocialSignal.ts,
                                             SocialSignal.posts).filter(SocialSignal.status == "OK").all():
        if (posts or 0) >= (TALK_VIDEOS if src == "youtube" else TALK_POSTS):
            social.setdefault(sid, []).append(ts)
    spikes = {}
    for b, ts in session.query(BrandBuzz.brand, BrandBuzz.ts).filter(BrandBuzz.spike.is_(True)).all():
        spikes.setdefault(_norm(b), []).append(ts)
    for c in done:
        lo, hi = c["ts"] - 86400, c["ts"] + TIP_DAYS * 86400
        co = _norm(c["company"])
        c["beyond"] = any(lo <= t <= hi for t in social.get(c["sid"], [])) or any(
            lo <= t <= hi for b, ts_ in spikes.items() if b and (b == co or b in co) for t in ts_)
    return done, latest


def calibrate(session, save=True):
    from db.models import TipRule
    from engine.reality import wilson
    cases, latest = replay(session)
    rates = {}
    for T in CANDIDATES:
        k = sum(1 for c in cases if c["max_outlets"] >= max(T, c["start"] + TIP_GROWTH) and c["beyond"])
        rates[T] = {"tipped": k, "cases": len(cases), "rate": round(k / len(cases), 3) if cases else None,
                    "range": wilson(k, len(cases))}
    beyond_share = round(sum(1 for c in cases if c["beyond"]) / len(cases), 3) if cases else None
    if len(cases) >= MIN_CASES:
        best = min(CANDIDATES, key=lambda T: (abs(rates[T]["rate"] - TARGET_RATE), T))
    else:
        best = DEFAULT_OUTLETS
    out = {"outlets": best, "growth": TIP_GROWTH, "days": TIP_DAYS, "cases": len(cases),
           "rate": rates[best]["rate"], "provisional": len(cases) < FREEZE_CASES,
           "chosen_by": "history" if len(cases) >= MIN_CASES else "default (too few finished cases)",
           "rates": rates, "beyond_news_share": beyond_share, "latest_reading": latest,
           "examples": sorted(({"title": c["title"], "company": c["company"], "start": c["start"],
                                "max": c["max_outlets"], "beyond": c["beyond"],
                                "tipped": c["max_outlets"] >= max(best, c["start"] + TIP_GROWTH) and c["beyond"]}
                               for c in cases), key=lambda x: -x["max"])[:25]}
    if save:
        session.add(TipRule(ts=time.time(), outlets=best, growth=TIP_GROWTH, days=TIP_DAYS, rate=out["rate"],
                            cases=len(cases), provisional=out["provisional"],
                            report=json.dumps({k: v for k, v in out.items() if k != "examples"} | {"examples": out["examples"]})))
        session.commit()
        print(f"🎯 tip rule: {best}+ outlets, +{TIP_GROWTH} since listing, seen beyond the news, within {TIP_DAYS} days "
              f"— {out['rate']} of {len(cases)} spreading stories tipped ({out['chosen_by']})")
    return out


def maybe_calibrate(session_factory):
    """No rule yet, or a provisional one older than a day → recalibrate in the background. Frozen after."""
    s = session_factory()
    try:
        rl = rule(s)
    finally:
        s.close()
    due = not rl["calibrated"] or (rl["provisional"] and time.time() - rl.get("ts", 0) > 86400)
    if not due or _cal["running"] or time.time() - _cal["ts"] < 3600:
        return
    _cal["running"], _cal["ts"] = True, time.time()

    def work():
        s2 = session_factory()
        try:
            calibrate(s2)
        except Exception as e:
            s2.rollback()
            print(f"⚠️ tip calibration error: {e}")
        finally:
            s2.close()
            _cal["running"] = False
    threading.Thread(target=work, daemon=True).start()
