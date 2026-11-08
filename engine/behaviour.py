"""
Behaviour recorder (test phase)
===============================
Do people actually ACT on a story? For each listed story that names a company, once a day
we ask Google Trends how often people searched for action phrases about it:
    EN: "boycott X", "cancel X", "X alternative"        DE: "X boykott", "X kündigen", "X alternative"
over the last 3 months (daily values). For each phrase:
    ratio = average of the last 7 days ÷ typical level of the 8 weeks before (median)
"acted" = some phrase at ≥ 1.5 × its normal level with real volume (last-7-day average ≥ 5).
(Bud Light 2023: "boycott bud light" went from 0 to 100 — far above 1.5×.
 Netflix 2023 password crackdown: "cancel netflix" ~1.7× its normal level.)

Nothing here settles bets yet — it is recorded so we can check, after 2–3 weeks, whether the
signal is reliable enough. Google Trends has no official free API; this uses the same calls
as the trends website. If Google refuses (HTTP 429 etc.) we back off for 6 hours.
"""

import json
import statistics
import time

import requests

from db.database import SessionLocal
from db.models import BehaviourSignal, Narrative

CHECK_EVERY_H   = 24
MAX_PER_RUN     = 4          # stories checked per collector run (gentle on Google)
PAUSE_S         = 6
BACKOFF_H       = 6
ACT_RATIO       = 1.5
ACT_MIN_VOLUME  = 5
_state = {"blocked_until": 0.0, "session": None}

TERMS = {
    "en": ["boycott {c}", "cancel {c}", "{c} alternative"],
    "de": ["{c} boykott", "{c} kündigen", "{c} alternative"],
}
GEO_FOR_REGION = [("US", "US", "en"), ("UK", "GB", "en"), ("EU", "DE", "de"), ("AU", "AU", "en")]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}


def _geo(regions):
    for reg, geo, lang in GEO_FOR_REGION:
        if reg in (regions or []):
            return geo, lang
    return "US", "en"


def _session():
    if _state["session"] is None:
        s = requests.Session()
        s.headers.update(UA)
        try:
            s.get("https://trends.google.com/trends/?geo=US", timeout=15)   # sets the NID cookie
        except Exception:
            pass
        _state["session"] = s
    return _state["session"]


def _strip(txt):
    i = txt.find("{")
    return json.loads(txt[i:]) if i >= 0 else {}


def fetch_series(terms, geo, timeframe="today 3-m"):
    """{term: [daily values]} (shared 0–100 scale) or raises."""
    s = _session()
    req = {"comparisonItem": [{"keyword": t, "geo": geo, "time": timeframe} for t in terms],
           "category": 0, "property": ""}
    r = s.get("https://trends.google.com/trends/api/explore",
              params={"hl": "en-US", "tz": "0", "req": json.dumps(req)}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"explore HTTP {r.status_code}")
    widgets = _strip(r.text).get("widgets", [])
    w = next((x for x in widgets if x.get("id") == "TIMESERIES"), None)
    if not w:
        raise RuntimeError("no timeseries widget")
    time.sleep(1.5)
    r2 = s.get("https://trends.google.com/trends/api/widgetdata/multiline",
               params={"hl": "en-US", "tz": "0", "req": json.dumps(w["request"]), "token": w["token"]},
               timeout=20)
    if r2.status_code != 200:
        raise RuntimeError(f"multiline HTTP {r2.status_code}")
    rows = _strip(r2.text).get("default", {}).get("timelineData", [])
    out = {t: [] for t in terms}
    for row in rows:
        vals = row.get("value") or []
        for i, t in enumerate(terms):
            out[t].append(int(vals[i]) if i < len(vals) else 0)
    return out


def assess(series):
    """Per-term ratio of the last 7 days against the 8 weeks before, and a verdict."""
    res, acted = {}, False
    for term, vals in series.items():
        if len(vals) < 21:
            res[term] = {"ratio": None, "recent": None, "baseline": None}
            continue
        recent = sum(vals[-7:]) / 7
        prev = vals[-63:-7] if len(vals) >= 63 else vals[:-7]
        base = statistics.median(prev) if prev else 0
        if base <= 0:
            base_mean = sum(prev) / len(prev) if prev else 0
            ratio = (recent / base_mean) if base_mean > 0 else (99.0 if recent > 0 else 0.0)
        else:
            ratio = recent / base
        ratio = round(min(ratio, 99.0), 2)
        res[term] = {"ratio": ratio, "recent": round(recent, 1), "baseline": round(base, 1)}
        if ratio >= ACT_RATIO and recent >= ACT_MIN_VOLUME:
            acted = True
    return {"terms": res, "acted": acted}


def check_story(n, session):
    snap = json.loads(n.snapshot or "{}")
    company = (snap.get("company") or "").strip()
    geo, lang = _geo(snap.get("regions"))
    rec = BehaviourSignal(sid=n.sid, ts=time.time(), geo=geo, company=company or None)
    if not company:
        rec.status, rec.error = "NO_DATA", "no company named in the story"
        session.add(rec)
        return rec
    terms = [t.format(c=company.lower()) for t in TERMS[lang]]
    try:
        series = fetch_series(terms, geo)
        if not any(series.values()):
            rec.status, rec.error = "NO_DATA", "Google Trends returned no data"
        else:
            rec.result, rec.series = json.dumps(assess(series)), json.dumps(series)
    except Exception as e:
        rec.status, rec.error = "ERROR", str(e)[:300]
        if "429" in str(e) or "HTTP 4" in str(e):
            _state["blocked_until"] = time.time() + BACKOFF_H * 3600
            _state["session"] = None
    session.add(rec)
    return rec


def run_behaviour_checks():
    if time.time() < _state["blocked_until"]:
        return
    session = SessionLocal()
    try:
        now = time.time()
        listed = session.query(Narrative).filter(Narrative.status == "LISTED").all()
        recent = {r.sid: r.ts for r in session.query(BehaviourSignal)
                  .filter(BehaviourSignal.ts >= now - CHECK_EVERY_H * 3600).all()}
        def _company(n):
            try:
                return (json.loads(n.snapshot or "{}").get("company") or "").strip()
            except Exception:
                return ""
        # only stories that name a company can be checked — the others don't use up the slots
        due = [n for n in listed if n.sid not in recent and _company(n)][:MAX_PER_RUN]
        for i, n in enumerate(due):
            rec = check_story(n, session)
            session.commit()
            v = json.loads(rec.result).get("acted") if rec.result else None
            print(f"🔎 behaviour {n.sid} [{rec.geo}] {rec.company or '—'}: {rec.status}"
                  + (f" — acted={v}" if rec.result else f" — {rec.error}"))
            if rec.status == "ERROR" and time.time() < _state["blocked_until"]:
                print(f"⏸ behaviour checks paused {BACKOFF_H}h (Google refused)")
                break
            if i < len(due) - 1:
                time.sleep(PAUSE_S)
    finally:
        session.close()


def latest_for(session, sid, limit=30):
    rows = (session.query(BehaviourSignal).filter(BehaviourSignal.sid == sid)
            .order_by(BehaviourSignal.ts.desc()).limit(limit).all())
    return [{"ts": r.ts, "geo": r.geo, "company": r.company, "status": r.status, "error": r.error,
             "result": json.loads(r.result) if r.result else None} for r in rows]
