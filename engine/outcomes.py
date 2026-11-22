"""
Outcomes closer to money (22 Nov 2026)
======================================
The track record checks warnings against what really happened. Wikipedia readers and share prices are a
rough sensor for "lost revenue", so three free measures closer to money are collected for every tracked
brand, slowly and in the background:

  app ratings   the brand's own iPhone app (found via Apple's free search), its newest App Store reviews
                per day: count, average stars, 1-star count. Angry customers rate the app down fast.
                Up to APPS_PER_RUN brands every APP_EVERY_H hours (Apple's public review feed, no key).
  brand search  Google search interest in the brand itself, 90 days daily (Trends). A demand / attention
                signal that covers private brands too. One brand per SEARCH_EVERY_H hours, gentle on Trends.
  revenue       quarterly revenue from SEC filings (XBRL "companyfacts"), growth vs the same quarter a year
                before. Only runs if the Render variable SEC_CONTACT is set: the SEC asks every caller to
                name a contact in its requests, and that is your choice to make, not mine.

Each becomes an outcome in reality.outcome(): search_x / search_jump, app_drop, revenue_slowdown.
"""

import json
import os
import re
import statistics
import time
from datetime import date, datetime, timedelta, timezone

import requests

from db.models import AppDaily, BrandApp, RevenueQuarter, SearchDaily

APPS_PER_RUN, APP_EVERY_H = 6, 3
SEARCH_EVERY_H = 1
REV_PER_RUN, REV_EVERY_H = 3, 1
_st = {"apps": 0.0, "search": 0.0, "rev": 0.0, "tickers": None}
_FOODISH = ("Food & Drink", "Shopping", "Lifestyle")


def _shown(brand):
    return " ".join(w.capitalize() if w.islower() else w for w in brand.split())


# ─────────────────────────────── app ratings ───────────────────────────────
def _find_app(brand):
    r = requests.get("https://itunes.apple.com/search", timeout=15,
                     params={"term": brand, "entity": "software", "country": "us", "limit": 8})
    if r.status_code != 200:
        raise RuntimeError(f"app search HTTP {r.status_code}")
    words = [w for w in re.findall(r"[a-z0-9]+", brand.lower().replace("'", "")) if len(w) > 1]
    for a in r.json().get("results", []):
        name = (a.get("trackName") or "") + " " + (a.get("sellerName") or "")
        low = name.lower().replace("'", "").replace("’", "")
        if a.get("primaryGenreName") in _FOODISH and all(w in low for w in words):
            return str(a.get("trackId")), (a.get("trackName") or "")[:120]
    return None, None


def _reviews(app_id, pages=3):
    out = []
    for p in range(1, pages + 1):
        r = requests.get(f"https://itunes.apple.com/us/rss/customerreviews/page={p}/id={app_id}/sortby=mostrecent/json",
                         timeout=15)
        if r.status_code != 200:
            break
        ents = (r.json().get("feed") or {}).get("entry") or []
        if isinstance(ents, dict):
            ents = [ents]
        got = 0
        for e in ents:
            rating = ((e.get("im:rating") or {}).get("label"))
            upd = ((e.get("updated") or {}).get("label") or "")[:10]
            if rating and re.match(r"\d{4}-\d\d-\d\d", upd):
                out.append((upd, int(rating)))
                got += 1
        if got < 40:
            break
    return out


def run_apps(session):
    from engine.reality import BRANDS
    if time.time() - _st["apps"] < APP_EVERY_H * 3600:
        return 0
    _st["apps"] = time.time()
    known = {a.brand: a for a in session.query(BrandApp).all()}
    todo = sorted(BRANDS, key=lambda b: known[b].ts if b in known else 0)[:APPS_PER_RUN]
    done = 0
    for b in todo:
        a = known.get(b)
        try:
            if not a or (not a.app_id and time.time() - a.ts > 30 * 86400):
                aid, name = _find_app(_shown(b))
                if not a:
                    a = BrandApp(brand=b, ts=time.time())
                    session.add(a)
                a.app_id, a.name = aid, name
            a.ts = time.time()
            if a.app_id:
                per_day = {}
                for day, stars in _reviews(a.app_id):
                    per_day.setdefault(day, []).append(stars)
                for day, st in per_day.items():
                    row = session.query(AppDaily).filter(AppDaily.brand == b, AppDaily.day == day).first()
                    if not row:
                        row = AppDaily(brand=b, day=day)
                        session.add(row)
                    if len(st) >= (row.n or 0):
                        row.n, row.avg, row.ones = len(st), round(sum(st) / len(st), 2), sum(1 for x in st if x == 1)
                done += 1
        except Exception as e:
            print(f"⚠️ app reviews {b}: {e}")
    session.commit()
    if done:
        print(f"📱 app reviews: {done} brands updated")
    return done


# ─────────────────────────────── brand search ───────────────────────────────
def _trends_daily(term):
    from engine import behaviour as B
    s = B._session()
    req = {"comparisonItem": [{"keyword": term, "geo": "US", "time": "today 3-m"}], "category": 0, "property": ""}
    r = s.get("https://trends.google.com/trends/api/explore", timeout=20,
              params={"hl": "en-US", "tz": "0", "req": json.dumps(req)})
    if r.status_code != 200:
        raise RuntimeError(f"explore HTTP {r.status_code}")
    w = next((x for x in B._strip(r.text).get("widgets", []) if x.get("id") == "TIMESERIES"), None)
    if not w:
        raise RuntimeError("no timeseries widget")
    time.sleep(1.5)
    r2 = s.get("https://trends.google.com/trends/api/widgetdata/multiline", timeout=20,
               params={"hl": "en-US", "tz": "0", "req": json.dumps(w["request"]), "token": w["token"]})
    if r2.status_code != 200:
        raise RuntimeError(f"multiline HTTP {r2.status_code}")
    out = {}
    for row in B._strip(r2.text).get("default", {}).get("timelineData", []):
        try:
            day = datetime.fromtimestamp(int(row["time"]), timezone.utc).date().isoformat()
            out[day] = float((row.get("value") or [0])[0])
        except Exception:
            pass
    return out


def run_search(session):
    from engine import behaviour as B
    from engine.reality import BRANDS
    if time.time() - _st["search"] < SEARCH_EVERY_H * 3600 or time.time() < B._state.get("blocked_until", 0):
        return 0
    _st["search"] = time.time()
    last = {b: ts for b, ts in session.query(SearchDaily.brand, SearchDaily.ts).all()}
    b = sorted(BRANDS, key=lambda x: last.get(x, 0))[0]
    try:
        series = _trends_daily(_shown(b))
    except Exception as e:
        if "429" in str(e):
            B._state["blocked_until"] = time.time() + B.BACKOFF_H * 3600
        print(f"⚠️ brand search {b}: {e}")
        return 0
    session.query(SearchDaily).filter(SearchDaily.brand == b).delete()      # Trends rescales every fetch
    now = time.time()
    for day, v in series.items():
        session.add(SearchDaily(brand=b, day=day, value=v, ts=now))
    session.commit()
    print(f"🔍 brand search: {b} — {len(series)} days")
    return 1


# ─────────────────────────────── revenue (SEC) ───────────────────────────────
_REV_CONCEPTS = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                 "RevenueFromContractWithCustomerIncludingAssessedTax")


def _sec_get(url):
    contact = os.getenv("SEC_CONTACT", "").strip()
    r = requests.get(url, timeout=25, headers={"User-Agent": f"Quantara research {contact}"})
    if r.status_code != 200:
        raise RuntimeError(f"SEC HTTP {r.status_code}")
    return r.json()


def _quarters(facts):
    usd = []
    for c in _REV_CONCEPTS:
        units = (((facts.get("facts") or {}).get("us-gaap") or {}).get(c) or {}).get("units", {}).get("USD") or []
        q = {}
        for u in units:
            try:
                s, e = date.fromisoformat(u["start"]), date.fromisoformat(u["end"])
            except Exception:
                continue
            if 80 <= (e - s).days <= 100:
                q[u["end"]] = float(u["val"])
        if len(q) >= 5:
            usd = sorted(q.items())
            break
    out = []
    for end, val in usd:
        e = date.fromisoformat(end)
        prev = [v for k, v in usd if 355 <= (e - date.fromisoformat(k)).days <= 375]
        out.append((end, val, round(val / prev[0] - 1, 4) if prev and prev[0] else None))
    return out


def run_revenue(session):
    from engine.reality import BRANDS
    if not os.getenv("SEC_CONTACT", "").strip() or time.time() - _st["rev"] < REV_EVERY_H * 3600:
        return 0
    _st["rev"] = time.time()
    if _st["tickers"] is None:
        try:
            d = _sec_get("https://www.sec.gov/files/company_tickers.json")
            _st["tickers"] = {v["ticker"].upper(): int(v["cik_str"]) for v in d.values()}
        except Exception as e:
            print(f"⚠️ SEC tickers: {e}")
            return 0
    last = {}
    for b, ts in session.query(RevenueQuarter.brand, RevenueQuarter.ts).all():
        last[b] = max(last.get(b, 0), ts)
    due = [b for b, (_, t) in BRANDS.items() if t and t.upper() in _st["tickers"]
           and time.time() - last.get(b, 0) > 7 * 86400]
    done = 0
    for b in due[:REV_PER_RUN]:
        try:
            cik = _st["tickers"][BRANDS[b][1].upper()]
            qs = _quarters(_sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"))
            session.query(RevenueQuarter).filter(RevenueQuarter.brand == b).delete()
            now = time.time()
            for end, val, yoy in qs:
                session.add(RevenueQuarter(brand=b, end=end, revenue=val, yoy=yoy, ts=now))
            done += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"⚠️ revenue {b}: {e}")
            session.add(RevenueQuarter(brand=b, end="0000-00-00", ts=time.time()))   # don't retry for a week
    session.commit()
    if done:
        print(f"💵 revenue: {done} companies updated")
    return done


def run(session):
    for f in (run_apps, run_search, run_revenue):
        try:
            f(session)
        except Exception as e:
            session.rollback()
            print(f"⚠️ outcomes {f.__name__}: {e}")


# ─────────────────────────────── as outcomes ───────────────────────────────
def search_x(series, day0, window=7):
    """Peak brand-search interest in the week after day0 against its normal level (days -45…-15)."""
    base = [v for k, v in series.items() if -45 <= (date.fromisoformat(k) - day0).days <= -15]
    after = [v for k, v in series.items() if -1 <= (date.fromisoformat(k) - day0).days <= window]
    if len(base) < 15 or len(after) < window:
        return None
    normal = max(statistics.median(base), 1.0)
    return round(max(after) / normal, 2)


def app_drop(days, day0):
    """True if the app's average rating in the week after day0 is 0.3+ stars below the month before."""
    before = [(n, a) for k, (n, a) in days.items() if -30 <= (date.fromisoformat(k) - day0).days <= -1 and a is not None]
    after = [(n, a) for k, (n, a) in days.items() if 0 <= (date.fromisoformat(k) - day0).days <= 7 and a is not None]
    nb, na = sum(n for n, _ in before), sum(n for n, _ in after)
    if nb < 20 or na < 10:
        return None
    mb = sum(n * a for n, a in before) / nb
    ma = sum(n * a for n, a in after) / na
    return (mb - ma) >= 0.3


def revenue_slowdown(quarters, day0):
    """True if revenue growth in the first quarter ending 30+ days after day0 is 2+ points below the
    quarter before the warning. None until that quarter is reported."""
    after = [(e, y) for e, y in quarters if y is not None and date.fromisoformat(e) >= day0 + timedelta(days=30)]
    before = [(e, y) for e, y in quarters if y is not None and date.fromisoformat(e) <= day0]
    if not after or not before:
        return None
    return (min(after)[1] - max(before)[1]) <= -0.02
