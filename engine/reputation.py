"""
Food Reputation Index (FRI) — the one number companies buy protection on
========================================================================
  FRI = 50% SAFETY  (the FSNI: recalls, outbreaks, FDA, GDELT — unchanged)
      + 50% SOCIAL  (food & drink stories people argue about)

SOCIAL = average of the 5 strongest food & drink stories (story score 0–100, empty slots
count 0). A story people are really talking about counts more: up to +50% for Bluesky
posts about it (40 posts in 24 h = the full +50%).

OUTLOOK (0–100) — where the index is likely heading, from four early signals:
  crowd     40%  players' bets: average chance they give on listed stories
  buzz      30%  food brands with a spike of COMPLAINTS on Bluesky right now (each brand = 25)
  momentum  30%  how fast the index rose over the last 24 h (+10 points = 100)
  searches   0%  share of checked stories where people search "boycott / cancel / alternative" — recorded and
                 shown, not counted until it proves itself (it fired on a stadium name and an obituary)
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
import re
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
WEIGHTS = {"crowd": 0.40, "buzz": 0.30, "searches": 0.0, "momentum": 0.30}   # searches: shown, not counted (28 Nov:
# brand searches like "cancel kroger" move on their own, whatever the story — not yet a proven signal)
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
    from engine.everyday import stakes, index_gate
    rows = []
    for s in stories:
        posts = talk.get(s["sid"], 0)
        level, mult = stakes([s.get("title", "")])
        # damage potential (price / safety / trust / boycott ×1.25 · nostalgia / novelty ×0.75) and talk (up to +50%)
        w = (s.get("score") or 0) * mult * (1 + 0.5 * min(1.0, posts / TALK_FULL))
        # no demand on the brand (jokes, listicles, taste tests) → no pressure, does not count
        ok, why = index_gate(s.get("title", ""), [], s.get("company"))   # the broad gate: real pressure, shown or not
        dem = why if ok else ""
        if not ok:
            w = 0.0
        rows.append({"sid": s["sid"], "title": s["title"], "score": round(s.get("score") or 0, 1),
                     "weighted": round(min(100.0, w), 1), "posts": posts, "company": s.get("company"),
                     "sector": s.get("sector"), "listed": bool(s.get("listed")), "url": s.get("url"), "stakes": level,
                     "source": s.get("source"), "demand": dem, "gate": None if ok else why,
                     "spread_r": s.get("spread_r"), "outlets": s.get("outlets") or 0, "regions": s.get("regions") or []})
    rows.sort(key=lambda x: x["weighted"], reverse=True)
    try:                                           # headline tone (test, recorded only): counting stories first
        from engine.tone import tones
        tn = tones(session, [x["title"] for x in rows])
        for x in rows:
            x["tone"] = tn.get(x["title"])
    except Exception as e:
        print(f"⚠️ tone error: {e}")
    try:                                           # who spreads it + on how many platforms (test, recorded only)
        from engine.voices import run_checks, attach
        run_checks(session, rows)
        attach(session, rows)
        from engine import comments as YC
        YC.run(session, rows)                      # what people write under the videos (every 2 h)
        YC.attach(session, rows)
        from engine import outcomes as OC
        OC.run(session)                            # app ratings, brand search, revenue — outcomes closer to money
        try:
            from engine import bridges as BR
            BR.run(session)                        # phase 2: does a listed story jump between groups on Bluesky
        except Exception as e:
            session.rollback()
            print(f"⚠️ bridges: {e}")
    except Exception as e:
        session.rollback()
        print(f"⚠️ voices error: {e}")
    _social.last_rows = rows                       # all stories, for the sector stocks
    top = rows[:TOP_N]
    social = sum(x["weighted"] for x in top) / TOP_N
    return round(social, 2), rows[:15]


# ─────────────────────────────────────────────
# OUTLOOK
# ─────────────────────────────────────────────
def _crowd(session):
    """The players' bets on the listed stories (weighted by how many people trade each)."""
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
    if not sum(t for _, t in rows):
        # nobody is betting yet: the prices are only our own starting chances, so leave the part out
        return None, {"stories": len(rows), "players": 0}
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
    # only a spike of COMPLAINTS counts; a spike of chatter ("custard after dinner") is not pressure
    bad = [x for x in r if x.get("trouble")]
    return min(100.0, 25.0 * len(bad)), {"rising": [x["brand"] for x in bad][:6],
                                         "chatter": [x["brand"] for x in r if not x.get("trouble")][:6]}


def _searches(session):
    rows = (session.query(BehaviourSignal).filter(BehaviourSignal.ts >= time.time() - 2 * DAY,
                                                  BehaviourSignal.status == "OK").all())
    if not rows:
        return None, {"checked": 0}
    from engine.behaviour import judge
    acted = sum(1 for r in rows if judge(json.loads(r.result or "{}")))
    return 100.0 * acted / len(rows), {"checked": len(rows), "acted": acted}


def _momentum(session, value):
    old = (session.query(ReputationTick).filter(ReputationTick.ts <= time.time() - DAY)
           .order_by(ReputationTick.ts.desc()).first())
    if not old:
        return None, {}
    d = value - old.value
    return max(0.0, min(100.0, d * 10)), {"change_24h": round(d, 2)}


# the index rules changed on 15 Nov 2026 (one gate for real pressure, no fluff): the "normal" level is
# built only from readings made with the current rules, so old and new are never compared
METHOD_SINCE = 1794772800.0   # 2026-11-15 20:00 UTC (22:00 Kyiv)


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
        if score is not None and WEIGHTS[name] > 0:
            used[name] = score
    if not used:
        return None, parts
    tw = sum(WEIGHTS[k] for k in used)
    return round(sum(WEIGHTS[k] * v for k, v in used.items()) / tw, 1), parts


# ─────────────────────────────────────────────
# READING (called every collector run)
# ─────────────────────────────────────────────
# The safety half is about US food. The FSNI behind it watches food safety in the US, UK, EU and Australia,
# so its value is scaled by how much of its current top stories are US food stories (28 Nov 2026).
# The shared parts (the FDA's US recall counts, the baseline) always keep 30%.
SAFETY_TOP_N = 8
SAFETY_KEEP = 0.3
FOREIGN_RX = re.compile(
    r"\b(canada|canadian|uk|u\.k\.|britain|british|england|scotland|scottish|wales|ireland|irish|"
    r"australia|australian|new zealand|germany|austria|france|italy|spain|netherlands|belgium|europe|european|eu|"
    r"india|china|japan|korea|philippines|singapore|malaysia|south africa|"
    r"tesco|sainsbury'?s?|asda|waitrose|morrisons|dunnes|rewe|edeka|kaufland|marks (?:&|and) spencer|"
    r"fsa|fsai|efsa|rasff|cfia)\b", re.I)
NON_ASCII_RX = re.compile(r"[äöüßÄÖÜ]")


def us_food_stories(stories):
    """The food-safety stories that are about US food: covered from the US, no foreign place or chain in the
    headline, not a German-language headline, not a medicine."""
    from engine.narrative_index import NON_FOOD_RX, PATHOGEN_RX
    out = []
    for f in stories:
        t = f.get("title") or ""
        if "US" not in (f.get("regions") or []):
            continue
        if FOREIGN_RX.search(t) or NON_ASCII_RX.search(t):
            continue
        if NON_FOOD_RX.search(t) and not PATHOGEN_RX.search(t):
            continue
        out.append(f)
    return out


def us_safety(session, fsni):
    """(scaled safety value, share of US food among the top stories, the US top stories)."""
    if fsni is None:
        return None, None, []
    from engine.narrative_index import _load_story_index
    top = sorted(_load_story_index(session, arena="food"), key=lambda x: -(x.get("score") or 0))[:SAFETY_TOP_N]
    tot = sum(max(0.0, x.get("score") or 0) for x in top)
    us = us_food_stories(top)
    if not tot:
        return round(fsni, 2), None, us
    share = sum(max(0.0, x.get("score") or 0) for x in us) / tot
    return round(fsni * (SAFETY_KEEP + (1 - SAFETY_KEEP) * share), 2), round(share, 2), us


# Themes see more than the index (28 Nov 2026): US food stories with real stakes that the index leaves out only
# because no national brand is named ("servers debate tip pooling", one LAX bar's tipping trick) still show where
# the industry's narratives are going. They count at half weight in the themes and never in the index.
THEME_ONLY_GATES = ("no US brand or US place named", "a single local business, not a national brand")
THEME_ONLY_WEIGHT = 0.5


def theme_only_rows(rows):
    from engine.everyday import STAKE_RX
    out = []
    for r in rows:
        if (r.get("weighted") or 0) > 0 or r.get("gate") not in THEME_ONLY_GATES:
            continue
        t = r.get("title") or ""
        if "US" not in (r.get("regions") or []) or FOREIGN_RX.search(t) or NON_ASCII_RX.search(t):
            continue
        if not any(rx.search(t) for rx in STAKE_RX.values()):
            continue
        out.append({**r, "weighted": round(THEME_ONLY_WEIGHT * (r.get("score") or 0), 1), "theme_only": True})
    return out


def compute_reputation():
    session = SessionLocal()
    try:
        fsni = _latest_fsni(session)
        safety, us_share, _ = us_safety(session, fsni)
        if fsni is not None:
            print(f"🇺🇸 safety half: FSNI {fsni:.1f} × US food share {us_share} → {safety}")
        social, top = _social(session)
        if safety is None and social is None:
            return
        value = round(SAFETY_W * (safety or 0) + SOCIAL_W * (social or 0), 2) if safety is not None and social is not None \
            else round(safety if social is None else social, 2)
        try:
            from engine.attention import method_start, refresh
            since_rules = method_start(session)              # the current rules' start (21 Nov 2026: wider net)
            refresh(session)                                 # learned brands → brand lists
        except Exception as e:
            since_rules = METHOD_SINCE
            print(f"⚠️ method start error: {e}")
        hist = [r.value for r in session.query(ReputationTick).filter(ReputationTick.ts >= max(time.time() - 7 * DAY,
                                                                                               since_rules)).all()]
        normal = round(statistics.median(hist), 2) if len(hist) >= 20 else None
        o, parts = outlook(session, value)
        session.add(ReputationTick(ts=time.time(), value=value, safety=safety, social=social, normal=normal,
                                   outlook=o, label=label_for(o), parts=json.dumps(parts), top=json.dumps(top)))
        session.commit()
        settle_due(session)
        try:
            from engine.sectors import compute_sectors, close_all
            compute_sectors(session, getattr(_social, "last_rows", []), safety)   # category info for companies
            close_all(session)                                                    # sector trading is retired
        except Exception as e:
            print(f"⚠️ sector stocks error: {e}")
        try:
            from engine import reality
            reality.log_signals(session, getattr(_social, "last_rows", []), value, o, label_for(o))
            reality.collect_in_background()          # pageviews + share prices, once a day
        except Exception as e:
            print(f"⚠️ reality check error: {e}")
        try:
            from engine.narratives import record as _narr
            # + the safety half's stories (recalls, outbreaks), so "Food safety scares" shows what pushes the index
            from engine.narrative_index import _load_story_index
            food = us_food_stories(sorted(_load_story_index(session, arena="food"),
                                          key=lambda x: -(x.get("score") or 0))[:SAFETY_TOP_N])
            safety_rows = [{"sid": f.get("sid"), "title": f.get("title"), "weighted": round(f.get("score") or 0, 1),
                            "company": f.get("company")} for f in food if (f.get("score") or 0) > 0]
            rows_ = getattr(_social, "last_rows", [])
            _narr(session, rows_ + theme_only_rows(rows_) + safety_rows)   # big narratives (test, recorded only)
        except Exception as e:
            session.rollback()
            print(f"⚠️ narratives error: {e}")
        try:
            from engine.attention import record_rows
            record_rows(session, getattr(_social, "last_rows", []))   # ridicule + new brands (test, recorded only)
        except Exception as e:
            session.rollback()
            print(f"⚠️ attention error: {e}")
        try:
            from engine.attention import run_youtube_discovery
            run_youtube_discovery()                  # every 2 h: food-drama videos → attention layer + news searches
        except Exception as e:
            print(f"⚠️ youtube discovery error: {e}")
        try:
            from engine.spots import resolve
            resolve(session, getattr(_social, "last_rows", []))   # did a spotted brand make the news?
        except Exception as e:
            session.rollback()
            print(f"⚠️ spots error: {e}")
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
        if (s.get("weighted") or 0) <= 0:          # only stories that really count in the index
            continue
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
