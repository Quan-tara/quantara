"""
REALITY CHECK — does what we measure match what really happens?

Two outside measures, both free:
  • Wikipedia pageviews (English, human readers only): sudden public attention on a brand.
    Free, CC0, needs only a descriptive User-Agent.
  • Share prices (Yahoo chart API): did the brand's share fall more than the S&P 500 afterwards?

Three uses:
  1. collect()        once a day: pageviews + closing prices for every tracked brand
  2. forward log      every warning is written down WHEN it happens (no hindsight possible):
                        signal  — a story starts pushing the index for a brand
                        listed  — a story is listed for players
                        outlook — the company outlook changes level (calm / watch / pressure)
  3. report()         hit rate, false-alarm rate and lift, on past listings and on the forward log

Honest limits: pageviews also jump for good news (a launch, an ad); share prices move for many
reasons, and private brands (Chick-fil-A, Subway…) have none. Both are checks, not proof.
"""
import json
import statistics
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import requests

from db.database import SessionLocal
from db.models import ForwardLog, Narrative, RealityDaily

UA = {"User-Agent": "QuantaraBot/1.0 (https://quantara-7fdc.onrender.com; food reputation research) requests"}
YAHOO_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Safari/537.36"}
BENCH = "^GSPC"
HISTORY_DAYS = 400          # first run: about a year of history, for the false-alarm test
SPIKE_X = 2.0               # attention counts as a spike at 2× the brand's normal level
WINDOW_D = 7                # look for the spike from the day before to 7 days after the warning
DAY = 86400

# brand → (English Wikipedia article, share ticker or None for private companies)
BRANDS = {
    "mcdonald's": ("McDonald's", "MCD"), "starbucks": ("Starbucks", "SBUX"),
    "chipotle": ("Chipotle_Mexican_Grill", "CMG"), "taco bell": ("Taco_Bell", "YUM"),
    "kfc": ("KFC", "YUM"), "pizza hut": ("Pizza_Hut", "YUM"), "wendy's": ("Wendy's", "WEN"),
    "burger king": ("Burger_King", "QSR"), "popeyes": ("Popeyes", "QSR"), "domino's": ("Domino's", "DPZ"),
    "papa john's": ("Papa_John's", "PZZA"), "jack in the box": ("Jack_in_the_Box", "JACK"),
    "wingstop": ("Wingstop", "WING"), "shake shack": ("Shake_Shack", "SHAK"),
    "cracker barrel": ("Cracker_Barrel", "CBRL"), "texas roadhouse": ("Texas_Roadhouse", "TXRH"),
    "olive garden": ("Olive_Garden", "DRI"), "chili's": ("Chili's", "EAT"), "denny's": ("Denny's", "DENN"),
    "applebee's": ("Applebee's", "DIN"), "ihop": ("IHOP", "DIN"), "dutch bros": ("Dutch_Bros_Coffee", "BROS"),
    "sweetgreen": ("Sweetgreen", "SG"), "krispy kreme": ("Krispy_Kreme", "DNUT"),
    "chick-fil-a": ("Chick-fil-A", None), "subway": ("Subway_(restaurant)", None),
    "jersey mike's": ("Jersey_Mike's_Subs", None), "dunkin": ("Dunkin'", None),
    "in-n-out": ("In-N-Out_Burger", None), "panera": ("Panera_Bread", None), "five guys": ("Five_Guys", None),
    "raising cane's": ("Raising_Cane's_Chicken_Fingers", None), "sonic": ("Sonic_Drive-In", None),
    "arby's": ("Arby's", None), "dairy queen": ("Dairy_Queen", "BRK-B"),
    "coca-cola": ("Coca-Cola", "KO"), "pepsi": ("Pepsi", "PEP"), "hershey": ("The_Hershey_Company", "HSY"),
    "kraft": ("Kraft_Heinz", "KHC"), "heinz": ("Kraft_Heinz", "KHC"), "mondelez": ("Mondelez_International", "MDLZ"),
    "oreo": ("Oreo", "MDLZ"), "bud light": ("Bud_Light", "BUD"), "anheuser-busch": ("Anheuser-Busch", "BUD"),
    "monster energy": ("Monster_Energy", "MNST"), "beyond meat": ("Beyond_Meat", "BYND"),
    "walmart": ("Walmart", "WMT"), "costco": ("Costco", "COST"), "target": ("Target_Corporation", "TGT"),
    "kroger": ("Kroger", "KR"), "albertsons": ("Albertsons", "ACI"), "trader joe's": ("Trader_Joe's", None),
    "whole foods": ("Whole_Foods_Market", None), "aldi": ("Aldi", None), "doordash": ("DoorDash", "DASH"),
    "instacart": ("Instacart", "CART"), "nestle": ("Nestlé", "NSRGY"), "nestlé": ("Nestlé", "NSRGY"),
}
ALIASES = {"mcdonalds": "mcdonald's", "dominos": "domino's", "dunkin'": "dunkin", "coke": "coca-cola",
           "kellogg's": None, "kraft heinz": "kraft"}

_lock = threading.Lock()
_state = {"running": False, "last": 0.0, "msg": "not run yet"}


def brand_key(name):
    """'McDonald’s' → "mcdonald's" if we track it, else None."""
    if not name:
        return None
    k = name.lower().replace("’", "'").strip()
    k = ALIASES.get(k, k)
    return k if k in BRANDS else None


# ─────────────────────────────────────────────
# 1. COLLECT (once a day, in the background)
# ─────────────────────────────────────────────
def _pageviews(article, start, end):
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
           f"{quote(article, safe='')}/daily/{start:%Y%m%d}00/{end:%Y%m%d}00")
    r = requests.get(url, headers=UA, timeout=30)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return {f"{i['timestamp'][:4]}-{i['timestamp'][4:6]}-{i['timestamp'][6:8]}": int(i["views"])
            for i in r.json().get("items", [])}


def _closes(ticker, days):
    p2 = int(time.time()) + DAY
    p1 = p2 - (days + 5) * DAY
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                     params={"period1": p1, "period2": p2, "interval": "1d"}, headers=YAHOO_UA, timeout=30)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        return {}
    ts = res[0].get("timestamp") or []
    ind = res[0].get("indicators") or {}
    cl = (ind.get("adjclose") or [{}])[0].get("adjclose") or (ind.get("quote") or [{}])[0].get("close") or []
    return {datetime.fromtimestamp(t, timezone.utc).date().isoformat(): float(c) for t, c in zip(ts, cl) if c}


def _store(session, key, values, field):
    if not values:
        return 0
    have = {r.day: r for r in session.query(RealityDaily).filter(RealityDaily.brand == key,
                                                                 RealityDaily.day.in_(list(values))).all()}
    for d, v in values.items():
        row = have.get(d)
        if row is None:
            row = RealityDaily(brand=key, day=d)
            session.add(row)
        setattr(row, field, v)
    session.commit()
    return len(values)


def collect(force=False):
    """Fetch pageviews and prices. Full year on the first run, last two weeks afterwards."""
    with _lock:
        if _state["running"] or (not force and time.time() - _state["last"] < 20 * 3600):
            return False
        _state["running"] = True
    session = SessionLocal()
    ok_w = ok_p = 0
    errs = []
    try:
        today = date.today()
        for key, (article, ticker) in BRANDS.items():
            try:
                n = session.query(RealityDaily).filter(RealityDaily.brand == key, RealityDaily.views.isnot(None)).count()
                start = today - timedelta(days=HISTORY_DAYS if n < 300 else 14)
                ok_w += bool(_store(session, key, _pageviews(article, start, today - timedelta(days=1)), "views"))
            except Exception as e:
                session.rollback()
                errs.append(f"{key}: {str(e)[:60]}")
            time.sleep(0.3)                               # one request at a time, gently
        for ticker in sorted({t for _, t in BRANDS.values() if t} | {BENCH}):
            try:
                n = session.query(RealityDaily).filter(RealityDaily.brand == "$" + ticker).count()
                ok_p += bool(_store(session, "$" + ticker, _closes(ticker, HISTORY_DAYS if n < 200 else 30), "close"))
            except Exception as e:
                session.rollback()
                errs.append(f"{ticker}: {str(e)[:60]}")
            time.sleep(0.5)
        _state["msg"] = (f"{datetime.now(timezone.utc):%d %b %H:%M} UTC · pageviews for {ok_w}/{len(BRANDS)} brands, "
                         f"prices for {ok_p} shares" + (f" · {len(errs)} errors (e.g. {errs[0]})" if errs else ""))
        print(f"🔬 reality check: {_state['msg']}")
        _state["last"] = time.time()
    finally:
        _state["running"] = False
        session.close()
    return True


def collect_in_background(force=False):
    if _state["running"] or (not force and time.time() - _state["last"] < 20 * 3600):
        return
    threading.Thread(target=collect, kwargs={"force": force}, daemon=True).start()


# ─────────────────────────────────────────────
# 2. FORWARD LOG (written at the moment of the warning)
# ─────────────────────────────────────────────
def log_signals(session, rows, fri, outlook, label):
    """Called with the index rows each reading. A story that starts pushing the index for a tracked
    brand is logged once ('signal'). An outlook level change is logged once ('outlook')."""
    try:
        from engine.everyday import brand_in
        now = time.time()
        known = {s for (s,) in session.query(ForwardLog.sid).filter(ForwardLog.kind == "signal",
                                                                    ForwardLog.ts >= now - 30 * DAY).all()}
        for r in rows:
            if (r.get("weighted") or 0) <= 0 or r.get("sid") in known:
                continue
            b = brand_key(r.get("company")) or brand_key(brand_in([r.get("title") or ""]))
            session.add(ForwardLog(ts=now, kind="signal", brand=b, sid=r.get("sid"), title=(r.get("title") or "")[:300],
                                   fri=fri, outlook=outlook, label=label, weight=r.get("weighted")))
        # 'spread': a counting story is spreading (R ≥ 1.3, 3+ new articles in 24 h) — logged once per story,
        # so later we can see whether spreading stories really hit brands more often than the rest
        spread_known = {s for (s,) in session.query(ForwardLog.sid).filter(ForwardLog.kind == "spread",
                                                                           ForwardLog.ts >= now - 30 * DAY).all()}
        for r in rows:
            sr = r.get("spread_r") or {}
            if (r.get("weighted") or 0) > 0 and sr.get("label") == "spreading" and r.get("sid") not in spread_known:
                b = brand_key(r.get("company")) or brand_key(brand_in([r.get("title") or ""]))
                session.add(ForwardLog(ts=now, kind="spread", brand=b, sid=r.get("sid"), title=(r.get("title") or "")[:300],
                                       fri=fri, outlook=outlook, label=f"R {sr.get('r')}", weight=r.get("weighted")))
        last = (session.query(ForwardLog).filter(ForwardLog.kind == "outlook").order_by(ForwardLog.ts.desc()).first())
        if label and (last is None or last.label != label):
            session.add(ForwardLog(ts=now, kind="outlook", fri=fri, outlook=outlook, label=label,
                                   title=f"outlook {'starts at' if last is None else 'moves to'} {label}"))
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"⚠️ forward log error: {e}")


def log_listed(session, sid, title, company):
    """Adds the entry to the caller's session; the caller commits (with the listing itself)."""
    try:
        from engine.everyday import brand_in
        b = brand_key(company) or brand_key(brand_in([title or ""]))
        session.add(ForwardLog(ts=time.time(), kind="listed", brand=b, sid=sid, title=(title or "")[:300]))
    except Exception as e:
        print(f"⚠️ forward log error: {e}")


# ─────────────────────────────────────────────
# 3. WHAT FOLLOWED
# ─────────────────────────────────────────────
def _series(session, key, field):
    rows = session.query(RealityDaily.day, getattr(RealityDaily, field)).filter(RealityDaily.brand == key).all()
    return {d: v for d, v in rows if v is not None}


def _d(s):
    return date.fromisoformat(s)


def attention(views, day0):
    """(ratio, peak_day) — highest pageviews from the day before to WINDOW_D days after day0, against the
    brand's normal level (median of days -45…-15). None if there is not enough data yet."""
    base = [views[k] for k in views if -45 <= (_d(k) - day0).days <= -15]
    after = [(views[k], k) for k in views if -1 <= (_d(k) - day0).days <= WINDOW_D]
    if len(base) < 15 or len(after) < WINDOW_D:
        return None, None
    normal = max(statistics.median(base), 50)
    peak, pday = max(after)
    return round(peak / normal, 2), pday


def _close_on(closes, day):
    ks = sorted(k for k in closes if _d(k) <= day)
    return closes[ks[-1]] if ks else None


def excess_return(closes, bench, day0, days):
    """Share return minus S&P 500 return from day0 to day0+days, in %. None if not there yet."""
    end = day0 + timedelta(days=days)
    if not closes or end > date.today() - timedelta(days=1):
        return None
    a0, a1, b0, b1 = _close_on(closes, day0), _close_on(closes, end), _close_on(bench, day0), _close_on(bench, end)
    if None in (a0, a1, b0, b1):
        return None
    return round(100 * ((a1 / a0) - (b1 / b0)), 2)


def outcome(session, brand, ts, cache):
    if brand not in BRANDS:
        return None
    day0 = datetime.fromtimestamp(ts, timezone.utc).date()
    if brand not in cache:
        t = BRANDS[brand][1]
        cache[brand] = (_series(session, brand, "views"), _series(session, "$" + t, "close") if t else {})
    if "$bench" not in cache:
        cache["$bench"] = _series(session, "$" + BENCH, "close")
    views, closes = cache[brand]
    x, pday = attention(views, day0)
    return {"attention_x": x, "spike": None if x is None else x >= SPIKE_X, "peak_day": pday,
            "excess_30d": excess_return(closes, cache["$bench"], day0, 30),
            "excess_90d": excess_return(closes, cache["$bench"], day0, 90), "listed_day": day0.isoformat()}


# ─────────────────────────────────────────────
# 4. REPORT — hit rate, false alarms, lift
# ─────────────────────────────────────────────
def _rate(xs):
    xs = [x for x in xs if x is not None]
    return (round(100 * sum(xs) / len(xs), 1), len(xs)) if xs else (None, 0)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return (round(sum(xs) / len(xs), 2), len(xs)) if xs else (None, 0)


_cache = {"ts": 0.0, "data": None}


def report(session, max_age=600):
    """Cached for 10 minutes — the calculation is heavy for a small server."""
    if _cache["data"] is not None and time.time() - _cache["ts"] < max_age:
        return _cache["data"]
    _cache["data"], _cache["ts"] = _report(session), time.time()
    return _cache["data"]


def _counts_today(title):
    try:
        from engine.everyday import index_gate
        return index_gate(title or "")[0]
    except Exception:
        return True


def _report(session):
    from engine.everyday import brand_in
    cache = {"$bench": _series(session, "$" + BENCH, "close")}

    # A) past listings (stories players could bet on), matched to a tracked brand
    # Warnings whose headline today's rules would not count (jokes, obituaries, investor pieces…)
    # are left out of every test, so a rule fixed later also cleans the record.
    left_out = {"past": 0, "forward": 0}
    past = []
    for n in session.query(Narrative).filter(Narrative.status.in_(["LISTED", "CLOSED"])).all():
        snap = {}
        try:
            snap = json.loads(n.snapshot or "{}")
        except Exception:
            pass
        b = brand_key((snap.get("bet") or {}).get("company")) or brand_key(brand_in([n.title or ""]))
        if not b:
            continue
        if not _counts_today(n.title):
            left_out["past"] += 1
            continue
        o = outcome(session, b, n.first_seen or n.listed_at, cache)
        if o:
            past.append({"sid": n.sid, "brand": b, "title": n.title, "arena": n.arena or "food", **o})

    # B) controls: the same brands on ordinary days (every 7th day, nowhere near a warning)
    warn_days = {}
    for p in past:
        warn_days.setdefault(p["brand"], []).append(_d(p["listed_day"]))
    for f in session.query(ForwardLog).filter(ForwardLog.brand.isnot(None)).all():
        warn_days.setdefault(f.brand, []).append(datetime.fromtimestamp(f.ts, timezone.utc).date())
    controls = []
    for b in BRANDS:
        if b not in cache:
            t = BRANDS[b][1]
            cache[b] = (_series(session, b, "views"), _series(session, "$" + t, "close") if t else {})
        views, closes = cache[b]
        if not views:
            continue
        days = sorted(_d(k) for k in views)
        d = days[0] + timedelta(days=50)
        while d <= date.today() - timedelta(days=WINDOW_D + 2):
            if all(abs((d - w).days) > 14 for w in warn_days.get(b, [])):
                x, _ = attention(views, d)
                if x is not None:
                    controls.append({"brand": b, "attention_x": x, "spike": x >= SPIKE_X,
                                     "excess_30d": excess_return(closes, cache.get("$bench", {}), d, 30)})
            d += timedelta(days=7)

    # C) recall: big attention jumps (≥3× normal) since our first listing — did we warn first?
    first = min((_d(p["listed_day"]) for p in past), default=None)
    big = []
    if first:
        for b in BRANDS:
            views = cache.get(b, ({}, {}))[0]
            for k in sorted(views):
                d = _d(k)
                if d < first:
                    continue
                base = [views[j] for j in views if -45 <= (_d(j) - d).days <= -15]
                if len(base) < 15:
                    continue
                if views[k] >= 3 * max(statistics.median(base), 50):
                    if not big or big[-1]["brand"] != b or (d - _d(big[-1]["day"])).days > 14:
                        warned = any(-7 <= (d - w).days <= 2 for w in warn_days.get(b, []))
                        big.append({"brand": b, "day": k, "x": round(views[k] / max(statistics.median(base), 50), 1),
                                    "warned": warned})

    hit, n_hit = _rate([p["spike"] for p in past])
    base_rate, n_ctl = _rate([c["spike"] for c in controls])
    ex_w, n_ex_w = _mean([p["excess_30d"] for p in past])
    ex_c, n_ex_c = _mean([c["excess_30d"] for c in controls])

    # D) forward log, with results where enough time has passed
    fwd = []
    test_cards = set()
    try:
        from db.models import Spot
        from engine.story_comments import is_moderator
        by_card = {}
        for sp in session.query(Spot).all():
            by_card.setdefault(sp.card_sid, []).append(sp.user_id)
        test_cards = {c for c, us in by_card.items() if all(is_moderator(u) for u in us)}
    except Exception:
        pass
    for f in session.query(ForwardLog).order_by(ForwardLog.ts.desc()).limit(2000).all():
        if f.kind == "spot" and f.sid in test_cards:
            continue
        if f.kind in ("signal", "spread", "listed") and f.title and not _counts_today(f.title):
            left_out["forward"] += 1
            continue
        o = outcome(session, f.brand, f.ts, cache) if f.brand else None
        fwd.append({"ts": f.ts, "kind": f.kind, "brand": f.brand, "title": f.title, "label": f.label,
                    "fri": f.fri, "weight": f.weight, "result": o})
    total = session.query(ForwardLog).count() - sum(1 for f in session.query(ForwardLog.sid).filter(ForwardLog.kind == "spot").all()
                                                    if f[0] in test_cards)
    total -= left_out["forward"]
    since = session.query(ForwardLog.ts).order_by(ForwardLog.ts).first()

    kinds = {}
    for f in fwd:
        k = kinds.setdefault(f["kind"], {"entries": 0, "checked": 0, "spikes": 0, "ex30": []})
        k["entries"] += 1
        o = f.get("result") or {}
        if o.get("spike") is not None:
            k["checked"] += 1
            k["spikes"] += 1 if o["spike"] else 0
        if o.get("excess_30d") is not None:
            k["ex30"].append(o["excess_30d"])
    by_kind = {k: {"entries": v["entries"], "checked": v["checked"],
                   "spike_rate": round(100 * v["spikes"] / v["checked"], 1) if v["checked"] else None,
                   "excess_30d": round(sum(v["ex30"]) / len(v["ex30"]), 2) if v["ex30"] else None}
               for k, v in kinds.items()}
    return {
        "collector": _state["msg"],
        "rules": {"spike": f"pageviews ≥ {SPIKE_X:g}× the brand's normal level, from the day before to "
                           f"{WINDOW_D} days after", "normal": "median of days −45…−15"},
        "past_test": {
            "warnings": n_hit, "hit_rate": hit, "controls": n_ctl, "base_rate": base_rate,
            "lift": round(hit / base_rate, 1) if hit is not None and base_rate else None,
            "excess_30d_after_warning": ex_w, "n_excess_warning": n_ex_w,
            "excess_30d_ordinary_days": ex_c, "n_excess_ordinary": n_ex_c,
            "big_jumps": len(big), "big_jumps_warned": sum(1 for x in big if x["warned"]),
            "missed": [x for x in big if not x["warned"]][-12:],
            "cases": sorted(past, key=lambda p: p["listed_day"], reverse=True)[:40],
        },
        "forward": {"since": since[0] if since else None, "entries": total, "latest": fwd[:60], "by_kind": by_kind},
        "left_out": left_out,
    }
