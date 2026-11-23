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
import re
import threading
import time

SPREAD_MIN_OUTLETS = 3
SPREAD_NEW_12H = 2
TIP_GROWTH = 3            # default growth since listing (calibrated: GROWTHS)
TIP_DAYS = 5
DEFAULT_OUTLETS = 6       # our outlet counts come from ~20 feeds + searches, far below the real number
CANDIDATES = (5, 6, 8, 10, 12, 15)
GROWTHS = (2, 3, 4)
CAL_ARENAS = ("everyday", "brands")
RULE_VERSION = 4          # a stored rule from an older version is recalibrated at once (4: real outlet counts)
MEASURE_SINCE = 1795371408   # 22 Nov 2026 evening: outlets counted with the wide reach search from here on. Older readings
                          # undercount (~3 outlets per story), so the rule is calibrated only on readings after this.
TARGET_RATE = 0.25
MIN_CASES = 10
FREEZE_CASES = 30
TALK_POSTS, TALK_VIDEOS = 5, 6
PACE_TAU_H = 24          # news pace fades: a story's next outlets come mostly within about a day
_cal = {"running": False, "ts": 0.0}


# ── what may be listed: real trouble for the brand, never a promo, a launch or good news ──
TROUBLE_RX = re.compile(
    r"\b(outrag\w*|furious|fury|angr\w*|anger|backlash|boycott\w*|slam\w*|blast\w*|fume\w*|complain\w*|"
    r"criticis\w*|criticiz\w*|petition\w*|protest\w*|rally|rallies|uproar|"
    r"sick|ill|illness\w*|poison\w*|hospitali\w*|outbreak\w*|recall\w*|contamina\w*|salmonella|listeria|e\.? ?coli|"
    r"lawsuit\w*|sue[sd]?|suing|class action|accus\w*|investigat\w*|fined?|fines|penalt\w*|violation\w*|"
    r"strike\w*|walk ?out\w*|union\w*|layoff\w*|lay(?:s|ing)? off|laid off|fired|firing|job cuts?|clos(?:e|es|ing|ures?) (?:\d+ )?(?:stores|locations|restaurants)|"
    r"price hikes?|hikes?|rais(?:e|es|ed|ing) prices?|pricier|overpriced|shrink\w*|smaller|fees?|surcharge\w*|scam\w*|rip-?off|"
    r"backtrack\w*|scandal\w*|controvers\w*|bans?|banned|halt\w*|shortage\w*|franchisees?|"
    r"outages?|stranded|meltdown|cancell?ations|overdraft|junk fees?|data breach\w*|hack(?:ed|ers?)|customer data)\b", re.I)
PROMO_RX = re.compile(
    r"\b(reveals?|unveils?|launch(?:es|ed)?|debuts?|introduc\w*|new (?:menu|meal|flavou?r|item|drink|collab)\w*|"
    r"collab\w*|partner(?:s|ship)? with|limited[- ]time|brings? back|is back|returns? (?:to|for)|giveaway|"
    r"free \w+ (?:day|deal)|deal of|coupon\w*|recipe\w*|review:|taste test|ranked|ranking|best \w+ (?:to|in))\b", re.I)
GOODNEWS_RX = re.compile(
    r"\b(recover\w*|rebound\w*|beats? (?:estimates|expectations)|sales (?:rise|jump|surge|grow)\w*|record (?:sales|profit)\w*|"
    r"wins?|award\w*|celebrat\w*|donat\w*|charity|praised?)\b", re.I)


def tip_gate(title, others=(), company=None):
    """(ok, reason): may this story be listed for 'Will it tip?'. Stricter than the index gate:
    real trouble for the brand must be in the headline, and promos, launches and good news never list."""
    from engine.everyday import index_gate
    ok, why = index_gate(title, list(others or []), company)
    if not ok:
        return False, why
    t = title or ""
    if GOODNEWS_RX.search(t):
        return False, "good news for the brand"
    if not TROUBLE_RX.search(t):
        return False, "no trouble for the brand in the headline" + (" (promo / launch)" if PROMO_RX.search(t) else "")
    if PROMO_RX.search(t) and not re.search(r"\b(outrag|furious|backlash|boycott|slam|angr|anger|sick|recall|sue|lawsuit)", t, re.I):
        return False, "promo / launch"
    return True, ""


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
    from engine.story_market import new_outlets
    now = now or time.time()
    cur = int(max(s.get("outlets") or 0, len(s.get("all_outlets") or [])))
    if cur < SPREAD_MIN_OUTLETS:
        return False
    if new_outlets(s.get("members"), now - 12 * 3600) < SPREAD_NEW_12H:
        return False
    return bool(tip_gate(s.get("title"), [m.get("title") for m in s.get("members", [])], s.get("company"))[0])


def _spike_about(words_json, title, company):
    """A brand's Bluesky spike counts only if people are posting about THIS story (shared topic words):
    big brands spike every few days for unrelated reasons."""
    from engine.story_market import _topic_words
    try:
        words = json.loads(words_json or "[]")
    except Exception:
        return False
    if isinstance(words, dict):
        words = list(words)
    spike = {str(w).lower().rstrip("s") for w in words if isinstance(w, str)}
    return bool(spike & _topic_words(title, company))


def beyond_news(session, sid, company, since, title=None):
    """Seen beyond the news since `since`: Bluesky posts or YouTube videos found for this story, or the
    brand spiking on Bluesky with posts about this story's topic."""
    from db.models import BrandBuzz, SocialSignal
    for src, posts in (session.query(SocialSignal.source, SocialSignal.posts)
                       .filter(SocialSignal.sid == sid, SocialSignal.status == "OK", SocialSignal.ts >= since).all()):
        if (posts or 0) >= (TALK_VIDEOS if src == "youtube" else TALK_POSTS):
            return True
    c = _norm(company)
    if c and title:
        q = session.query(BrandBuzz.brand, BrandBuzz.words).filter(BrandBuzz.spike.is_(True), BrandBuzz.ts >= since).all()
        if any((_norm(b) == c or (_norm(b) and _norm(b) in c)) and _spike_about(w, title, company) for b, w in q):
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
def replay(session, arenas=CAL_ARENAS, include_open=False, since=None):
    """Every story that became Spreading in the stored readings (everyday + brand-scandal arenas), with
    what followed within TIP_DAYS: [{sid, title, company, ts, start, max_outlets, beyond}] — finished
    windows only. Beyond the news = posts / videos about the story, or the brand spiking on Bluesky,
    AFTER it started spreading."""
    from db.models import ArenaReading, BrandBuzz, SocialSignal
    from engine.everyday import brand_in
    from engine.story_market import _topic_words
    since = MEASURE_SINCE if since is None else since
    hist, started, latest, last_ts, alias = {}, {}, None, {}, {}
    q = (session.query(ArenaReading.ts, ArenaReading.arena, ArenaReading.story_index)
         .filter(ArenaReading.arena.in_(list(arenas)), ArenaReading.ts >= since).order_by(ArenaReading.ts))
    for ts, arena, idx in q.yield_per(200):
        if ts - last_ts.get(arena, 0) < 3 * 3600:
            continue
        last_ts[arena] = ts
        latest = ts
        try:
            stories = json.loads(idx or "[]")
        except Exception:
            continue
        for s in stories:
            sid, o = s.get("sid"), int(s.get("outlets") or 0)
            if not sid:
                continue
            sid = alias.get(sid, sid)          # the same story under a new id (headlines regroup)
            h = hist.setdefault(sid, [])
            h.append((ts, o))
            if sid in started:
                st = started[sid]
                if ts <= st["ts"] + TIP_DAYS * 86400:
                    st["max_outlets"] = max(st["max_outlets"], o)
                    st["path"].append((ts, o))
                continue
            before = [x for t, x in h if t <= ts - 12 * 3600]
            new12 = o - (before[-1] if before else 0)
            company = s.get("company") or brand_in([s.get("title") or ""])
            if o >= SPREAD_MIN_OUTLETS and new12 >= SPREAD_NEW_12H and company \
                    and tip_gate(s.get("title"), [], company)[0]:
                tw = _topic_words(s.get("title"), company)
                twin = next((c for c in started.values() if _norm(c["company"]) == _norm(company)
                             and ts - c["ts"] <= 7 * 86400 and _topic_words(c["title"], c["company"]) & tw), None)
                if twin:
                    alias[s.get("sid")] = twin["sid"]
                    if ts <= twin["ts"] + TIP_DAYS * 86400:
                        twin["max_outlets"] = max(twin["max_outlets"], o)
                        twin["path"].append((ts, o))
                    continue
                started[sid] = {"sid": sid, "title": (s.get("title") or "")[:160], "company": company,
                                "arena": arena, "ts": ts, "start": o, "max_outlets": o, "path": [(ts, o)]}
    if latest is None:
        return [], None
    for c in started.values():
        c["done"] = c["ts"] + TIP_DAYS * 86400 <= latest
    done = list(started.values()) if include_open else [c for c in started.values() if c["done"]]
    social = {}
    for sid, src, ts, posts in session.query(SocialSignal.sid, SocialSignal.source, SocialSignal.ts,
                                             SocialSignal.posts).filter(SocialSignal.status == "OK").all():
        if (posts or 0) >= (TALK_VIDEOS if src == "youtube" else TALK_POSTS):
            social.setdefault(sid, []).append(ts)
    spikes = {}
    for b, ts, w in session.query(BrandBuzz.brand, BrandBuzz.ts, BrandBuzz.words).filter(BrandBuzz.spike.is_(True)).all():
        spikes.setdefault(_norm(b), []).append((ts, w))
    for c in done:
        lo, hi = c["ts"], c["ts"] + TIP_DAYS * 86400
        co = _norm(c["company"])
        talk = [t for t in social.get(c["sid"], []) if lo <= t <= hi]
        talk += [t for b, xs in spikes.items() if b and (b == co or b in co)
                 for t, w in xs if lo <= t <= hi and _spike_about(w, c["title"], c["company"])]
        c["beyond"] = bool(talk)
        c["talk_at"] = min(talk) if talk else None
    return done, latest


def calibrate(session, save=True, since=None):
    from db.models import TipRule
    from engine.reality import wilson
    cases, latest = replay(session, since=since)
    for c in cases:
        c.pop("path", None)
    rates = {}
    for T in CANDIDATES:
        for G in GROWTHS:
            k = sum(1 for c in cases if c["max_outlets"] >= max(T, c["start"] + G) and c["beyond"])
            rates[f"{T}+{G}"] = {"outlets": T, "growth": G, "tipped": k, "cases": len(cases),
                                 "rate": round(k / len(cases), 3) if cases else None, "range": wilson(k, len(cases))}
    beyond_share = round(sum(1 for c in cases if c["beyond"]) / len(cases), 3) if cases else None
    if len(cases) >= MIN_CASES:
        # closest to a quarter tipping; on a tie the stricter rule (bigger, then more growth)
        key = min(rates, key=lambda k_: (abs(rates[k_]["rate"] - TARGET_RATE), -rates[k_]["outlets"], -rates[k_]["growth"]))
        best, growth = rates[key]["outlets"], rates[key]["growth"]
    else:
        best, growth, key = DEFAULT_OUTLETS, TIP_GROWTH, f"{DEFAULT_OUTLETS}+{TIP_GROWTH}"
    out = {"version": RULE_VERSION, "outlets": best, "growth": growth, "days": TIP_DAYS, "cases": len(cases),
           "rate": rates[key]["rate"], "provisional": len(cases) < FREEZE_CASES,
           "chosen_by": "history" if len(cases) >= MIN_CASES else "default (too few finished cases)",
           "rates": rates, "beyond_news_share": beyond_share, "latest_reading": latest,
           "examples": sorted(({"title": c["title"], "company": c["company"], "start": c["start"],
                                "max": c["max_outlets"], "beyond": c["beyond"],
                                "tipped": c["max_outlets"] >= max(best, c["start"] + growth) and c["beyond"]}
                               for c in cases), key=lambda x: -x["max"])[:25]}
    if save:
        session.add(TipRule(ts=time.time(), outlets=best, growth=growth, days=TIP_DAYS, rate=out["rate"],
                            cases=len(cases), provisional=out["provisional"],
                            report=json.dumps({k: v for k, v in out.items() if k != "examples"} | {"examples": out["examples"]})))
        session.commit()
        print(f"🎯 tip rule: {best}+ outlets, +{growth} since listing, seen beyond the news, within {TIP_DAYS} days "
              f"— {out['rate']} of {len(cases)} spreading stories tipped ({out['chosen_by']})")
    return out


def maybe_calibrate(session_factory):
    """No rule yet, or a provisional one older than a day → recalibrate in the background. Frozen after."""
    s = session_factory()
    try:
        rl = rule(s)
    finally:
        s.close()
    due = (not rl["calibrated"] or (rl.get("report") or {}).get("version") != RULE_VERSION
           or (rl["provisional"] and time.time() - rl.get("ts", 0) > 86400))
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


# ─────────────────────────────── board preview ───────────────────────────────
def preview(session, days=10):
    """What the board would have shown over the last `days` days under today's rules: every story that
    started spreading (max DAILY_CAP a day, first come), its question, and how it ended."""
    from engine.story_listing import rules_for
    rl = rule(session)
    cap = rules_for("everyday").get("daily_cap") or 6
    cases, latest = replay(session, arenas=("everyday",), include_open=True, since=0)
    if not latest:
        return {"rule": rl, "days": [], "latest": None}
    since = latest - days * 86400
    cases = sorted([c for c in cases if c["ts"] >= since], key=lambda c: c["ts"])
    by_day, out = {}, []
    for c in cases:
        day = time.strftime("%Y-%m-%d", time.gmtime(c["ts"]))
        if by_day.get(day, 0) >= cap:
            continue
        by_day[day] = by_day.get(day, 0) + 1
        target = target_for(c["start"], rl)
        reach = next((t for t, o in c["path"] if o >= target), None)
        tip_at = max(reach, c["talk_at"]) if reach and c.get("talk_at") else None
        result = "TIPPED" if tip_at else ("FADED" if c["done"] else "OPEN")
        out.append({"day": day, "ts": c["ts"], "title": c["title"], "company": c["company"], "start": c["start"],
                    "target": target, "max_outlets": c["max_outlets"], "beyond_news": c["beyond"],
                    "result": result, "tipped_after_h": round((tip_at - c["ts"]) / 3600) if tip_at else None})
    days_list = []
    for i in range(days, -1, -1):
        d = time.strftime("%Y-%m-%d", time.gmtime(latest - i * 86400))
        days_list.append({"day": d, "stories": [x for x in out if x["day"] == d]})
    n = len(out)
    return {"rule": {k: rl.get(k) for k in ("outlets", "growth", "days", "rate", "cases", "provisional")},
            "latest": latest, "listed": n, "per_day": round(n / max(1, days), 1),
            "tipped": sum(1 for x in out if x["result"] == "TIPPED"), "faded": sum(1 for x in out if x["result"] == "FADED"),
            "open": sum(1 for x in out if x["result"] == "OPEN"), "days": days_list}
