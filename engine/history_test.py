"""
Test on stored history (22 Nov 2026)
====================================
Instead of waiting for new warnings to mature, replay what is already in the database:

  every stored story reading (arena_readings, one per 3 h per arena)  →  today's rules  →  brand warnings
  →  what really happened afterwards (Wikipedia attention, share move beyond the market, and the new
     outcome sensors where they have history)  →  compared with ordinary brand-weeks of the same period.

Three groups, from weakest to strongest signal — if the index measures something real, the hit rate
should rise from group to group ("dose → response"):
  baseline   a tracked brand in 5+ outlets in one reading, no rules at all (the bar to beat)
  counting   a story that passes today's index gate with score ≥ 15
  strong     a story that passes today's index gate with score ≥ 30 (would reach the listing bar)
One warning per brand per week per group, like the live forward log.

Honest limits: the rules were tuned on some of these same headlines (in-sample), the period is short,
and many brands have no share price. A first answer, not proof — the forward log stays the judge.
(engine/backtest.py is a different, older study: 16 famous past cases rebuilt from Google Trends.)
Runs in the background on request; cached 6 h.
"""

import json
import threading
import time

DAY = 86400
SAMPLE_S = 3 * 3600
WEEK_S = 7 * DAY
_cache = {"ts": 0.0, "data": None, "running": False, "error": None}


def _events(session):
    from db.models import ArenaReading
    from engine.everyday import brand_in, index_gate
    from engine.reality import brand_key
    last_ts, first, latest, n_used = {}, None, None, 0
    seen = {"baseline": {}, "counting": {}, "strong": {}}
    events = {"baseline": [], "counting": [], "strong": []}
    q = session.query(ArenaReading.ts, ArenaReading.arena, ArenaReading.story_index).order_by(ArenaReading.ts)
    for ts, arena, idx in q.yield_per(200):
        if ts - last_ts.get(arena, 0) < SAMPLE_S:
            continue
        last_ts[arena] = ts
        n_used += 1
        first = first or ts
        latest = ts
        try:
            stories = json.loads(idx or "[]")
        except Exception:
            continue
        outlets = {}
        for s in stories:
            t = s.get("title") or ""
            b = brand_key(s.get("company")) or brand_key(brand_in([t]))
            if not b:
                continue
            outlets[b] = outlets.get(b, 0) + (s.get("outlets") or 0)
            score = s.get("score") or 0
            if score >= 15 and index_gate(t, [], s.get("company"))[0]:
                for g, bar in (("counting", 15), ("strong", 30)):
                    if score >= bar and ts - seen[g].get(b, -1e18) >= WEEK_S:
                        seen[g][b] = ts
                        events[g].append({"brand": b, "ts": ts, "title": t[:160], "score": round(score, 1)})
        for b, o in outlets.items():
            if o >= 5 and ts - seen["baseline"].get(b, -1e18) >= WEEK_S:
                seen["baseline"][b] = ts
                events["baseline"].append({"brand": b, "ts": ts, "title": f"{o} outlets", "score": None})
    return events, first, latest, n_used


def _summ(rows):
    from engine.reality import wilson, mean_range
    checked = [r for r in rows if r.get("spike") is not None]
    spikes = sum(1 for r in checked if r["spike"])
    ab = [r["abnormal_30d"] for r in rows if r.get("abnormal_30d") is not None]
    sj = [r for r in rows if r.get("search_jump") is not None]
    ad = [r for r in rows if r.get("app_drop") is not None]
    rv = [r for r in rows if r.get("revenue_slowdown") is not None]
    rate = lambda xs, k: round(100 * sum(1 for x in xs if x[k]) / len(xs), 1) if xs else None
    return {"cases": len(rows), "checked": len(checked),
            "spike_rate": round(100 * spikes / len(checked), 1) if checked else None,
            "range": wilson(spikes, len(checked)),
            "abnormal_30d": round(sum(ab) / len(ab), 2) if ab else None, "n_abnormal": len(ab),
            "abnormal_range": mean_range(ab),
            "search_jump_rate": rate(sj, "search_jump"), "n_search": len(sj),
            "app_drop_rate": rate(ad, "app_drop"), "n_app": len(ad),
            "revenue_slowdown_rate": rate(rv, "revenue_slowdown"), "n_revenue": len(rv)}


def compute(session):
    from engine.reality import BRANDS, outcome
    events, first, latest, n_used = _events(session)
    cache, out_groups, sample = {}, {}, []
    for g, evs in events.items():
        rows = []
        for e in evs:
            o = outcome(session, e["brand"], e["ts"], cache) or {}
            rows.append(o)
            if g == "strong":
                sample.append({**e, "spike": o.get("spike"), "attention_x": o.get("attention_x"),
                               "abnormal_30d": o.get("abnormal_30d"), "search_x": o.get("search_x"),
                               "app_drop": o.get("app_drop")})
        out_groups[g] = _summ(rows)
    # controls: every tracked brand, one day per week over the same period, minus weeks with any warning
    controls = []
    if first and latest:
        warned = {(e["brand"], int(e["ts"] // WEEK_S)) for evs in events.values() for e in evs}
        t = first
        while t <= latest:
            for b in BRANDS:
                if (b, int(t // WEEK_S)) in warned:
                    continue
                o = outcome(session, b, t, cache)
                if o:
                    controls.append(o)
            t += WEEK_S
    out_groups["ordinary"] = _summ(controls)
    sample.sort(key=lambda x: -x["ts"])
    return {"computed_at": time.time(), "period": [first, latest], "readings_used": n_used,
            "groups": out_groups, "strong_cases": sample[:30],
            "note": "Today's rules replayed on stored readings. In-sample and short: a first answer, not proof."}


def get(session_factory):
    """Cached result; starts a background run when stale. {'status': 'computing'} while it runs."""
    fresh = _cache["data"] and time.time() - _cache["ts"] < 6 * 3600
    if not fresh and not _cache["running"]:
        _cache["running"] = True

        def work():
            s = session_factory()
            try:
                _cache["data"], _cache["ts"], _cache["error"] = compute(s), time.time(), None
                print("🔁 history test done")
            except Exception as e:
                _cache["error"] = str(e)[:300]
                print(f"⚠️ history test error: {e}")
            finally:
                s.close()
                _cache["running"] = False
        threading.Thread(target=work, daemon=True).start()
    if _cache["data"]:
        return {**_cache["data"], "status": "ready", "refreshing": _cache["running"]}
    if _cache["error"] and not _cache["running"]:
        return {"status": "error", "error": _cache["error"]}
    return {"status": "computing"}
