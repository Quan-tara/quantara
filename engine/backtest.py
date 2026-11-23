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
    dict(key="target-pride-2023", name="Target Pride boycott", day="2023-05-24", type="social",
         ticker="TGT", bench="^GSPC", geo="US",
         attention=["target pride", "target"], action=["boycott target", "target boycott"]),
]
for _c in CASES:
    _c.setdefault("group", "blowup")

# 22 Nov 2026 — stories that looked like trouble and BLEW OVER (outrage, boycott calls, scary reports),
# so the test can ask the real question: in the first 3 days, do blow-ups look different from these?
FIZZLES = [
    dict(key="sbux-redcup-2015", name="Starbucks red cups", day="2015-11-09", ticker="SBUX", bench="^GSPC", geo="US",
         attention=["starbucks red cup", "starbucks"], action=["boycott starbucks", "starbucks alternative"]),
    dict(key="sbux-racetogether-2015", name="Starbucks Race Together", day="2015-03-17", ticker="SBUX", bench="^GSPC", geo="US",
         attention=["race together starbucks", "starbucks"], action=["boycott starbucks", "starbucks alternative"]),
    dict(key="sbux-philly-2018", name="Starbucks Philadelphia arrests", day="2018-04-14", ticker="SBUX", bench="^GSPC", geo="US",
         attention=["starbucks arrest", "starbucks"], action=["boycott starbucks", "starbucks alternative"]),
    dict(key="pepsi-jenner-2017", name="Pepsi Kendall Jenner ad", day="2017-04-04", ticker="PEP", bench="^GSPC", geo="US",
         attention=["pepsi ad", "pepsi"], action=["boycott pepsi", "pepsi alternative"]),
    dict(key="coke-georgia-2021", name="Coca-Cola Georgia voting law", day="2021-04-01", ticker="KO", bench="^GSPC", geo="US",
         attention=["coca cola georgia", "coca cola"], action=["boycott coca cola", "boycott coke"]),
    dict(key="coke-training-2021", name="Coca-Cola 'be less white' training", day="2021-02-19", ticker="KO", bench="^GSPC", geo="US",
         attention=["coca cola be less white", "coca cola"], action=["boycott coca cola", "boycott coke"]),
    dict(key="bk-tweet-2021", name="Burger King 'kitchen' tweet", day="2021-03-08", ticker="QSR", bench="^GSPC", geo="US",
         attention=["burger king tweet", "burger king"], action=["boycott burger king", "burger king alternative"]),
    dict(key="kfc-germany-2022", name="KFC Germany Kristallnacht message", day="2022-11-09", ticker="YUM", bench="^GSPC", geo="",
         attention=["kfc kristallnacht", "kfc"], action=["boycott kfc", "kfc apology"]),
    dict(key="tacobell-beef-2011", name="Taco Bell beef lawsuit", day="2011-01-24", ticker="YUM", bench="^GSPC", geo="US",
         attention=["taco bell beef", "taco bell"], action=["taco bell beef lawsuit", "boycott taco bell"]),
    dict(key="wendys-surge-2024", name="Wendy's surge pricing", day="2024-02-26", ticker="WEN", bench="^GSPC", geo="US",
         attention=["wendys surge pricing", "wendys"], action=["boycott wendys", "wendys dynamic pricing"]),
    dict(key="oreo-pride-2012", name="Oreo rainbow cookie", day="2012-06-25", ticker="MDLZ", bench="^GSPC", geo="US",
         attention=["oreo rainbow", "oreo"], action=["boycott oreo", "boycott kraft"]),
    dict(key="khc-macaroni-2017", name="Kraft mac & cheese chemicals report", day="2017-07-12", ticker="KHC", bench="^GSPC", geo="US",
         attention=["mac and cheese chemicals", "kraft mac and cheese"], action=["is kraft mac and cheese safe", "phthalates mac and cheese"]),
    dict(key="khc-lunchables-2024", name="Lunchables lead report", day="2024-04-09", ticker="KHC", bench="^GSPC", geo="US",
         attention=["lunchables lead", "lunchables"], action=["are lunchables safe", "lunchables petition"]),
    dict(key="gis-cheerios-2018", name="Cheerios glyphosate report", day="2018-08-15", ticker="GIS", bench="^GSPC", geo="US",
         attention=["cheerios glyphosate", "cheerios"], action=["are cheerios safe", "glyphosate cereal"]),
    dict(key="heineken-ad-2018", name="Heineken 'lighter is better' ad", day="2018-03-26", ticker="HEIA.AS", bench="^AEX", geo="",
         attention=["heineken ad", "heineken"], action=["boycott heineken", "heineken racist"]),
    dict(key="dove-ad-2017", name="Dove body-wash ad", day="2017-10-07", ticker="ULVR.L", bench="^FTSE", geo="",
         attention=["dove ad", "dove"], action=["boycott dove", "dove racist"]),
    dict(key="benjerrys-2021", name="Ben & Jerry's West Bank", day="2021-07-19", ticker="ULVR.L", bench="^FTSE", geo="US",
         attention=["ben and jerry's israel", "ben and jerry's"], action=["boycott ben and jerry's", "ben and jerry's boycott"]),
    dict(key="gillette-2019", name="Gillette 'toxic masculinity' ad", day="2019-01-14", ticker="PG", bench="^GSPC", geo="US",
         attention=["gillette ad", "gillette"], action=["boycott gillette", "gillette alternative"]),
    dict(key="nike-kaepernick-2018", name="Nike Kaepernick ad", day="2018-09-03", ticker="NKE", bench="^GSPC", geo="US",
         attention=["nike kaepernick", "nike"], action=["boycott nike", "burn nike"]),
    dict(key="tims-app-2022", name="Tim Hortons app tracking", day="2022-06-01", ticker="QSR", bench="^GSPC", geo="CA",
         attention=["tim hortons app", "tim hortons"], action=["tim hortons privacy", "delete tim hortons app"]),
    dict(key="keurig-2017", name="Keurig Hannity boycott", day="2017-11-11", ticker=None, bench=None, geo="US",
         attention=["keurig hannity", "keurig"], action=["boycott keurig", "smash keurig"]),
    dict(key="subway-tuna-2021", name="Subway tuna lawsuit", day="2021-01-26", ticker=None, bench=None, geo="US",
         attention=["subway tuna", "subway"], action=["is subway tuna real", "subway tuna lawsuit"]),
    dict(key="subway-footlong-2013", name="Subway footlong length", day="2013-01-17", ticker=None, bench=None, geo="US",
         attention=["subway footlong", "subway"], action=["subway footlong 11 inches", "subway lawsuit"]),
]
for _c in FIZZLES:
    _c.update(group="fizzle", type="fizzle")
CASES = CASES + FIZZLES
EARLY_D = 3                   # "the first 3 days": what we would have known in time to warn

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

    early = [d for d in days if 0 <= rel(d) <= EARLY_D]
    out = {"key": case["key"], "name": case["name"], "type": case["type"], "event_day": ev,
           "group": case.get("group", "blowup"),
           "early_attention_x": max((att_r.get(d, 0) for d in early), default=None) if att_r else None,
           "early_action_x": max((act_r.get(d, 0) for d in early), default=None) if act_r else None,
           "early_news_x": max((news_r.get(d, 0) for d in early), default=None) if news_r else None,
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
    # match the run by a small time window, not exact equality (the database can round a stored float)
    rows = (session.query(BacktestResult).filter(BacktestResult.run_ts >= last.run_ts - 5,
                                                 BacktestResult.run_ts <= last.run_ts + 5)
            .order_by(BacktestResult.id).all())
    cases = []
    for r in rows:
        d = json.loads(r.result) if r.result else {"key": r.key}
        d.update(_persistence(d))
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
                "lead_days": "days from the first signal jump to the first 5% share drop (positive = signal first)",
                "early_*": "the highest level in the first 3 days (what we would have known in time to warn)",
                "group": "blowup = a remembered crisis; fizzle = outrage / boycott calls / scary report that blew over"},
            "summary": summary(cases),
            "cases": cases}


# what each case was about (fizzles are typed "fizzle" in the list): safety / product-quality vs social
TOPIC = {"khc-macaroni-2017": "safety", "khc-lunchables-2024": "safety", "gis-cheerios-2018": "safety",
         "tacobell-beef-2011": "safety", "subway-tuna-2021": "safety", "subway-footlong-2013": "social"}
PERSIST_D = (7, 21)           # "does it keep going?": days 7–21 after the event


def _persistence(d):
    """From the stored daily Google Trends values of the story term (0–100 within the case's window, so
    comparable inside one case): how much of the first-days peak is still there in days 7–21, and whether
    a second wave comes. No new data is fetched."""
    case = next((c for c in CASES if c["key"] == d.get("key")), None)
    ser = ((d.get("series") or {}).get("raw_trends") or {})
    out = {"topic": TOPIC.get(d.get("key")) or (case or {}).get("type") or "social"}
    if out["topic"] == "fizzle":
        out["topic"] = "social"
    if not case or not ser or not d.get("event_day"):
        return out
    term = next((t for t in case["attention"] if t in ser), None)
    if not term:
        return out
    ev = _d(d["event_day"])
    rel = {(_d(k) - ev).days: v for k, v in ser[term].items()}
    early = max((v for k, v in rel.items() if 0 <= k <= EARLY_D), default=0)
    later = [v for k, v in rel.items() if PERSIST_D[0] <= k <= PERSIST_D[1]]
    wave2 = max((v for k, v in rel.items() if PERSIST_D[0] <= k <= 30), default=0)
    if early <= 0 or not later:
        return out
    out.update({"persist_7_21": round(sum(later) / len(later) / early, 3),        # mean of days 7–21 vs the early peak
                "second_wave": round(wave2 / early, 3)})                            # biggest later day vs the early peak
    act = next((t for t in case["action"] if t in ser), None)
    if act:
        r2 = {(_d(k) - ev).days: v for k, v in ser[act].items()}
        e2 = max((v for k, v in r2.items() if 0 <= k <= EARLY_D), default=0)
        l2 = [v for k, v in r2.items() if PERSIST_D[0] <= k <= PERSIST_D[1]]
        if e2 > 0 and l2:
            out["action_persist_7_21"] = round(sum(l2) / len(l2) / e2, 3)
    return out


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def summary(cases):
    """The question: in the first 3 days, do blow-ups look different from stories that blew over — and do
    their shares really end up different? Plus how well one simple early rule separates them."""
    out = {}
    for g in ("blowup", "fizzle"):
        cs = [c for c in cases if c.get("group", "blowup") == g and c.get("status") != "ERROR"]
        priced = [c for c in cs if c.get("excess_60d") is not None]
        out[g] = {"cases": len(cs), "with_share_price": len(priced),
                  "early_attention_x": _med([c.get("early_attention_x") for c in cs]),
                  "early_action_x": _med([c.get("early_action_x") for c in cs]),
                  "early_news_x": _med([c.get("early_news_x") for c in cs]),
                  "share_vs_market_60d": _med([c.get("excess_60d") for c in priced]),
                  "share_down_5pct_60d": f"{sum(1 for c in priced if c['excess_60d'] <= -5)} of {len(priced)}"}
    rules = {}
    for a, b in ((10, 3), (20, 3), (20, 10), (50, 5), (100, 10)):
        flag = lambda c: (c.get("early_attention_x") or 0) >= a and (c.get("early_action_x") or 0) >= b
        bl = [c for c in cases if c.get("group", "blowup") == "blowup" and c.get("status") != "ERROR"]
        fz = [c for c in cases if c.get("group") == "fizzle" and c.get("status") != "ERROR"]
        flagged = [c for c in bl + fz if flag(c)]
        fp = [c for c in flagged if c.get("excess_60d") is not None]
        rules[f"attention ≥{a}× and action ≥{b}× in 3 days"] = {
            "blowups_flagged": f"{sum(1 for c in bl if flag(c))} of {len(bl)}",
            "fizzles_flagged": f"{sum(1 for c in fz if flag(c))} of {len(fz)}",
            "share_vs_market_60d_when_flagged": _med([c["excess_60d"] for c in fp]),
            "share_vs_market_60d_when_not": _med([c.get("excess_60d") for c in bl + fz if not flag(c) and c.get("excess_60d") is not None])}
    out["early_rules"] = rules

    # 23 Nov 2026 — does it KEEP GOING? (persistence, days 7–21) and WHAT is it about? (safety vs social)
    def grp(g, cs_=None):
        return [c for c in (cs_ or cases) if c.get("group", "blowup") == g and c.get("status") != "ERROR"]
    for g in ("blowup", "fizzle"):
        cs = grp(g)
        out[g]["persist_7_21"] = _med([c.get("persist_7_21") for c in cs])
        out[g]["second_wave"] = _med([c.get("second_wave") for c in cs])
        out[g]["action_persist_7_21"] = _med([c.get("action_persist_7_21") for c in cs])
        out[g]["first_share_hit_day"] = _med([c.get("first_hit_day") for c in cs])
    prules = {}
    for th in (0.1, 0.2, 0.3, 0.5):
        flag = lambda c: (c.get("persist_7_21") or 0) >= th
        bl, fz = grp("blowup"), grp("fizzle")
        known = [c for c in bl + fz if c.get("persist_7_21") is not None]
        prules[f"still ≥{int(th * 100)}% of the early peak in days 7–21"] = {
            "blowups_flagged": f"{sum(1 for c in bl if c.get('persist_7_21') is not None and flag(c))} of {sum(1 for c in bl if c.get('persist_7_21') is not None)}",
            "fizzles_flagged": f"{sum(1 for c in fz if c.get('persist_7_21') is not None and flag(c))} of {sum(1 for c in fz if c.get('persist_7_21') is not None)}",
            "share_vs_market_60d_when_flagged": _med([c.get("excess_60d") for c in known if flag(c)]),
            "share_vs_market_60d_when_not": _med([c.get("excess_60d") for c in known if not flag(c)])}
    out["persistence_rules"] = prules
    topics = {}
    for t in ("safety", "social"):
        cs = [c for c in cases if c.get("topic") == t and c.get("status") != "ERROR"]
        pr = [c for c in cs if c.get("excess_60d") is not None]
        topics[t] = {"blowups": sum(1 for c in cs if c.get("group", "blowup") == "blowup"),
                     "fizzles": sum(1 for c in cs if c.get("group") == "fizzle"),
                     "share_vs_market_60d": _med([c["excess_60d"] for c in pr]),
                     "share_down_5pct_60d": f"{sum(1 for c in pr if c['excess_60d'] <= -5)} of {len(pr)}"}
    out["by_topic"] = topics
    out["read_me_persistence"] = ("persist_7_21 = average search level in days 7–21 as a share of the first-3-days "
                                  "peak (0.1 = faded to 10%). If blow-ups stay clearly higher than fizzles and the "
                                  "flagged cases lose more share value, 'does it keep going?' tells costly trouble "
                                  "from noise — about a week in, compare with first_share_hit_day to see if that is "
                                  "still early enough.")
    out["read_me"] = ("If blow-ups show clearly higher early numbers than fizzles, and the flagged cases lose more "
                      "share value than the unflagged ones, early signals can tell trouble that costs money from "
                      "noise. If the two groups look alike, they can't — and the index needs other inputs.")
    return out
