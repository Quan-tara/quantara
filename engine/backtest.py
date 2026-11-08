"""
Back-test — would our signals have seen past food reputation events coming?
===========================================================================
For each past case (see CASES) we rebuild, day by day from 60 days before to 90 days after
the event:
  • attention  — Google Trends interest in the story ("chipotle e coli")
  • action     — Google Trends for action phrases ("boycott bud light", "X alternative")
  • news       — article count per day from GDELT (only exists from 2017 on)
  • price      — the company's share price against its home market (excess return)
and turn attention + action into an approximate daily READING (0–100), the same idea as the
food reputation index.

Per case we then ask:
  • when did the reading first jump (≥ 3× normal)?
  • when did the share price first fall 5% below the market — and how far did it fall?
  • lead = days between the two (positive = our signal came first)

Runs on the server (it needs Google Trends, GDELT and Yahoo Finance, all free).
Start: GET /api/backtest/run     Results: GET /api/backtest/results  (?full=1 for daily series)
Research only — nothing here touches the index, the game or any contract.
"""

import json
import math
import statistics
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests

from db.database import SessionLocal
from db.models import BacktestResult

# key, name, event day, type, share ticker, home-market benchmark, Trends region ("" = worldwide),
# attention terms (story), action terms (people acting). Max 5 Trends terms per case.
CASES = [
    dict(key="chipotle-ecoli-2015", name="Chipotle E. coli", day="2015-10-31", type="safety",
         ticker="CMG", bench="^GSPC", geo="US",
         attention=["chipotle e coli", "chipotle"], action=["chipotle closed", "chipotle alternative"]),
    dict(key="maggi-ban-2015", name="Nestlé India Maggi ban", day="2015-06-05", type="safety",
         ticker="NESTLEIND.NS", bench="^NSEI", geo="IN",
         attention=["maggi lead", "maggi"], action=["maggi ban", "maggi alternative"]),
    dict(key="romaine-2018", name="US romaine E. coli", day="2018-04-13", type="safety",
         ticker=None, bench=None, geo="US",
         attention=["romaine e coli", "romaine lettuce"], action=["is romaine safe", "romaine alternative"]),
    dict(key="abbott-formula-2022", name="Abbott formula recall", day="2022-02-17", type="safety",
         ticker="ABT", bench="^GSPC", geo="US",
         attention=["similac recall", "similac"], action=["formula shortage", "similac alternative"]),
    dict(key="fonterra-danone-2013", name="Fonterra/Danone botulism scare", day="2013-08-03", type="safety",
         ticker="BN.PA", bench="^FCHI", geo="",
         attention=["fonterra botulism", "fonterra"], action=["nutricia recall", "karicare recall"]),
    dict(key="horsemeat-2013", name="UK horsemeat (Tesco)", day="2013-01-15", type="safety",
         ticker="TSCO.L", bench="^FTSE", geo="GB",
         attention=["horse meat", "tesco horse meat"], action=["tesco burgers", "horse meat burgers"]),
    dict(key="mcd-ecoli-2024", name="McDonald's E. coli", day="2024-10-22", type="safety",
         ticker="MCD", bench="^GSPC", geo="US",
         attention=["mcdonalds e coli", "quarter pounder"], action=["boycott mcdonalds", "mcdonalds alternative"]),
    dict(key="budlight-2023", name="Bud Light boycott", day="2023-04-01", type="social",
         ticker="BUD", bench="^GSPC", geo="US",
         attention=["bud light controversy", "bud light"], action=["boycott bud light", "bud light alternative"]),
    dict(key="papajohns-2018", name="Papa John's founder scandal", day="2018-07-11", type="social",
         ticker="PZZA", bench="^GSPC", geo="US",
         attention=["papa john schnatter", "papa johns"], action=["boycott papa johns", "papa johns alternative"]),
    dict(key="crackerbarrel-2025", name="Cracker Barrel logo", day="2025-08-19", type="social",
         ticker="CBRL", bench="^GSPC", geo="US",
         attention=["cracker barrel logo", "cracker barrel"], action=["boycott cracker barrel", "cracker barrel alternative"]),
    dict(key="starbucks-2023", name="Starbucks boycott", day="2023-10-17", type="social",
         ticker="SBUX", bench="^GSPC", geo="US",
         attention=["starbucks boycott", "starbucks"], action=["boycott starbucks", "starbucks alternative"]),
    dict(key="mcd-prices-2024", name="McDonald's price anger (approx. date)", day="2024-05-20", type="social",
         ticker="MCD", bench="^GSPC", geo="US",
         attention=["mcdonalds prices", "18 big mac"], action=["boycott mcdonalds", "mcdonalds too expensive"]),
    dict(key="kellogg-2024", name="WK Kellogg 'cereal for dinner'", day="2024-02-21", type="social",
         ticker="KLG", bench="^GSPC", geo="US",
         attention=["kellogg cereal for dinner", "kelloggs"], action=["boycott kellogg", "kellogg boycott"]),
    dict(key="chipotle-portions-2024", name="Chipotle portions (approx. date)", day="2024-05-10", type="social",
         ticker="CMG", bench="^GSPC", geo="US",
         attention=["chipotle portions", "chipotle"], action=["boycott chipotle", "chipotle alternative"]),
    dict(key="chickfila-2012", name="Chick-fil-A boycott", day="2012-07-16", type="social",
         ticker=None, bench=None, geo="US",
         attention=["chick fil a controversy", "chick fil a"], action=["boycott chick fil a", "chick fil a appreciation day"]),
    dict(key="goya-2020", name="Goya boycott", day="2020-07-09", type="social",
         ticker=None, bench=None, geo="US",
         attention=["goya boycott", "goya"], action=["boycott goya", "buy goya"]),
]

BEFORE_D, AFTER_D = 60, 90
JUMP_RATIO        = 3.0       # reading "jumped" = attention ≥ 3× its normal level
MIN_VOLUME        = 5
PRICE_HIT         = -5.0      # share "hit" = 5% below the market
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
_state = {"running": False, "started": None, "done": 0, "total": 0, "current": None, "last_run": None}


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _days(start, end):
    out, d = [], start
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# ── Google Trends (daily for windows under 270 days) ──
def _trends(case, start, end):
    from engine.behaviour import fetch_series, _state as bstate
    # Trends scales every request to its biggest term, so a big brand name would flatten the
    # small story terms to 0. Two requests: the small terms together, the brand name alone.
    groups = [case["attention"][:1] + case["action"], case["attention"][1:]]
    out = {}
    for terms in groups:
        if not terms:
            continue
        last_err = None
        for attempt in range(3):
            try:
                out.update(fetch_series(terms, case["geo"], timeframe=f"{start.isoformat()} {end.isoformat()}"))
                break
            except Exception as e:
                last_err = e
                bstate["session"] = None              # fresh cookie next time
                time.sleep(45 * (attempt + 1))
        else:
            raise RuntimeError(f"trends: {last_err}")
        time.sleep(5)
    return out


# ── GDELT article counts per day (2017 onward) ──
def _gdelt(case, start, end):
    if start < date(2017, 1, 1):
        return None
    q = " OR ".join(f'"{t}"' for t in case["attention"][:1] + case["action"][:1])
    params = {"query": f"({q})", "mode": "timelinevolraw", "format": "json",
              "startdatetime": start.strftime("%Y%m%d000000"), "enddatetime": end.strftime("%Y%m%d235959")}
    for attempt in range(3):
        r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params, headers=UA, timeout=40)
        if r.status_code == 429:
            time.sleep(20 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"gdelt HTTP {r.status_code}")
        try:
            tl = r.json().get("timeline") or []
        except Exception:
            raise RuntimeError("gdelt: not JSON (query too long or no data)")
        per_day = {}
        for p in (tl[0].get("data") if tl else []) or []:
            day = datetime.strptime(p["date"][:8], "%Y%m%d").date().isoformat()
            per_day[day] = per_day.get(day, 0) + (p.get("value") or 0)
        return per_day
    raise RuntimeError("gdelt HTTP 429")


# ── Yahoo Finance daily closes ──
def _closes(ticker, start, end):
    p1 = int(datetime.combine(start - timedelta(days=10), datetime.min.time(), timezone.utc).timestamp())
    p2 = int(datetime.combine(end + timedelta(days=2), datetime.min.time(), timezone.utc).timestamp())
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                     params={"period1": p1, "period2": p2, "interval": "1d"}, headers=UA, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"yahoo {ticker} HTTP {r.status_code}")
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        raise RuntimeError(f"yahoo {ticker}: no data")
    ts = res[0].get("timestamp") or []
    cl = ((res[0].get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") \
        or ((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    return {datetime.fromtimestamp(t, timezone.utc).date().isoformat(): c for t, c in zip(ts, cl) if c}


def _excess(case, start, end, ev):
    """{day: stock return − market return since the last close before the event, in %}"""
    s, m = _closes(case["ticker"], start, end), _closes(case["bench"], start, end)
    base_day = max((d for d in s if d < ev and d in m), default=None)
    if not base_day:
        raise RuntimeError("no price before the event")
    s0, m0 = s[base_day], m[base_day]
    return {d: round(100 * (s[d] / s0 - 1) - 100 * (m[d] / m0 - 1), 2) for d in sorted(s) if d in m and d >= base_day}


# ── Reading: attention + action vs their normal level → 0–100 ──
def _baseline(vals_by_day, days, ev):
    pre = [vals_by_day.get(d, 0) for d in days if d < (_d(ev) - timedelta(days=10)).isoformat()]
    pos = [v for v in pre if v > 0]
    return statistics.median(pre) if pre and statistics.median(pre) > 0 else (sum(pos) / len(pre) if pos else 0)


def _ratio(v, base):
    """times the normal level; tiny values (under MIN_VOLUME) never count as a jump"""
    if v < MIN_VOLUME:
        return 1.0 if base <= 0 else min(1.0, v / base)
    return v / base if base > 0 else 20.0


def _scale(r):
    """ratio → 0–100: 1× = 0, 20× (or more) = 100, logarithmic"""
    return 0.0 if r <= 1 else min(100.0, 100 * math.log(r) / math.log(20))


def _analyse(case, days, trends, news, price):
    ev = case["day"]
    att_terms, act_terms = case["attention"], case["action"]
    series = {}
    if trends:
        for t in att_terms + act_terms:
            if t in trends:
                series[t] = dict(zip(days, trends[t]))
    reading, att_r, act_r = {}, {}, {}
    if trends:
        bases = {t: _baseline(series[t], days, ev) for t in series}
        for d in days:
            a = max((_ratio(series[t].get(d, 0), bases[t]) for t in att_terms if t in series), default=1.0)
            b = max((_ratio(series[t].get(d, 0), bases[t]) for t in act_terms if t in series), default=1.0)
            att_r[d], act_r[d] = round(a, 2), round(b, 2)
            reading[d] = round(0.6 * _scale(a) + 0.4 * _scale(b), 1)
    news_r = {}
    if news:
        nb = _baseline(news, days, ev)
        news_r = {d: round(_ratio(news.get(d, 0), nb), 2) for d in days}

    rel = lambda d: (_d(d) - _d(ev)).days
    first_jump = next((d for d in days if att_r.get(d, 0) >= JUMP_RATIO), None)
    first_news = next((d for d in days if news_r.get(d, 0) >= JUMP_RATIO), None)
    first_act = next((d for d in days if act_r.get(d, 0) >= JUMP_RATIO), None)
    peak_day = max(reading, key=reading.get) if reading else None

    out = {"key": case["key"], "name": case["name"], "type": case["type"], "event_day": ev,
           "ticker": case["ticker"],
           "reading_peak": reading.get(peak_day) if peak_day else None,
           "reading_peak_day": rel(peak_day) if peak_day else None,
           "attention_peak_x": max(att_r.values()) if att_r else None,
           "action_peak_x": max(act_r.values()) if act_r else None,
           "first_jump_day": rel(first_jump) if first_jump else None,
           "first_action_day": rel(first_act) if first_act else None,
           "first_news_jump_day": rel(first_news) if first_news else None,
           "news_peak_x": max(news_r.values()) if news_r else None}
    if price:
        pdays = sorted(price)
        after = [d for d in pdays if d >= ev]
        at = lambda n: price[after[min(n, len(after) - 1)]] if after else None
        worst = min(after, key=lambda d: price[d]) if after else None
        hit = next((d for d in pdays if price[d] <= PRICE_HIT), None)
        out.update({"excess_5d": at(5), "excess_20d": at(20), "excess_60d": at(60),
                    "worst_excess": price[worst] if worst else None,
                    "worst_day": rel(worst) if worst else None,
                    "first_hit_day": rel(hit) if hit else None})
        if first_jump and hit:
            out["lead_days"] = (_d(hit) - _d(first_jump)).days
    # one-line verdict
    sig = (out["attention_peak_x"] or 0) >= JUMP_RATIO
    loss = price and (out.get("worst_excess") or 0) <= PRICE_HIT
    out["verdict"] = ("signal + share hit" if sig and loss else "signal, no share hit" if sig and price
                      else "share hit, no signal" if loss else "signal (no listed share)" if sig
                      else "no signal")
    out["series"] = {"reading": reading, "attention_x": att_r, "action_x": act_r, "news_x": news_r,
                     "excess_return": price or {}, "raw_trends": series}
    return out


def _run_case(case):
    ev = _d(case["day"])
    start, end = ev - timedelta(days=BEFORE_D), ev + timedelta(days=AFTER_D)
    days = _days(start, end)
    errors, trends, news, price = [], None, None, None
    try:
        trends = _trends(case, start, end)
    except Exception as e:
        errors.append(str(e)[:150])
    time.sleep(6)
    try:
        news = _gdelt(case, start, end)
    except Exception as e:
        errors.append(str(e)[:150])
    if case["ticker"]:
        try:
            price = _excess(case, start, end, case["day"])
        except Exception as e:
            errors.append(str(e)[:150])
    res = _analyse(case, days, trends, news, price)
    res["errors"] = errors
    status = "OK" if not errors else ("PARTIAL" if (trends or price) else "ERROR")
    return status, res


def _run_all():
    run_ts = time.time()
    _state.update(running=True, started=run_ts, done=0, total=len(CASES))
    try:
        for case in CASES:
            _state["current"] = case["name"]
            session = SessionLocal()
            try:
                try:
                    status, res = _run_case(case)
                    rec = BacktestResult(run_ts=run_ts, key=case["key"], status=status,
                                         result=json.dumps(res), error="; ".join(res["errors"])[:500] or None)
                except Exception as e:
                    rec = BacktestResult(run_ts=run_ts, key=case["key"], status="ERROR", error=str(e)[:500])
                st, err = rec.status, rec.error
                session.add(rec)
                session.commit()
            finally:
                session.close()
            _state["done"] += 1
            print(f"🧪 backtest {_state['done']}/{len(CASES)}: {case['name']} — {st}"
                  + (f" ({err[:80]})" if err else ""))
            time.sleep(8)
    finally:
        _state.update(running=False, current=None, last_run=run_ts)
        print(f"🧪 backtest finished: {len(CASES)} cases")


def start():
    if _state["running"]:
        return {"started": False, "message": "already running", **progress()}
    threading.Thread(target=_run_all, daemon=True).start()
    return {"started": True, "message": f"running {len(CASES)} cases — takes about 5–10 minutes"}


def progress():
    return {"running": _state["running"], "done": _state["done"], "total": _state["total"],
            "current": _state["current"]}


def results(session, full=False):
    last = session.query(BacktestResult).order_by(BacktestResult.run_ts.desc()).first()
    if not last:
        return {"progress": progress(), "cases": [], "message": "no run yet — open /api/backtest/run"}
    rows = (session.query(BacktestResult).filter(BacktestResult.run_ts == last.run_ts)
            .order_by(BacktestResult.id).all())
    cases = []
    for r in rows:
        d = json.loads(r.result) if r.result else {"key": r.key}
        if not full:
            d.pop("series", None)
        d["status"], d["error"] = r.status, r.error
        cases.append(d)
    return {"progress": progress(),
            "run_at": datetime.fromtimestamp(last.run_ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "how_to_read": {
                "reading": "approximate food-reputation reading 0–100 from Google Trends (60% attention, 40% action)",
                "*_x": "times the normal level (median of the 50 days before the event)",
                "*_day": "days relative to the event day (negative = before)",
                "excess_*": "share return minus home-market return since the day before the event, in %",
                "lead_days": "days from the first signal jump to the first 5% share drop (positive = signal first)"},
            "cases": cases}
