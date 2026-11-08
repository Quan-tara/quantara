"""
Story trading stats — history per user, leaderboard, founding members
=====================================================================
Everything is computed from the permanent trade log (story_trades) plus payouts
(story_positions), so positions closed early keep their real numbers.

A "call" = one user's GROW or FADE on one story (all trades on that side together).
  paid      = credits spent opening
  received  = credits from closing early + payout at close (or refund)
  open      = what the units still held would fetch if closed now
  profit    = received + open − paid
A call is FINISHED when nothing is held any more. A win = finished with profit > 0.

Leaderboard
  all time  — profit on every call (open ones at their close-now value)
  this week — profit on calls finished in the last 7 days
Founding members: the first FOUNDING_SEATS people to make a story trade, numbered by
the time of their first trade. Moderator accounts are left out of both.
"""

import time
from collections import defaultdict

from db.models import Narrative, StoryMarket, StoryPosition, StoryTrade

FOUNDING_SEATS = 100
WEEK_S = 7 * 86400


def _moderators():
    from engine.constants import ADMIN_ID, MM_USER_ID
    return {ADMIN_ID, MM_USER_ID}


def founding_numbers(session):
    """{user_id: seat number} for the first FOUNDING_SEATS traders."""
    import sqlalchemy as sa
    mods = _moderators()
    rows = session.execute(sa.text(
        "SELECT user_id, MIN(ts) AS t FROM story_trades GROUP BY user_id ORDER BY t")).fetchall()
    out, k = {}, 0
    for uid, _ in rows:
        if uid in mods:
            continue
        k += 1
        if k > FOUNDING_SEATS:
            break
        out[uid] = k
    return out


def _calls(session, user_ids=None):
    """{user_id: [call, …]}"""
    from engine.story_market import close_value, base_for
    tq = session.query(StoryTrade)
    pq = session.query(StoryPosition)
    if user_ids is not None:
        tq = tq.filter(StoryTrade.user_id.in_(list(user_ids) or [-1]))
        pq = pq.filter(StoryPosition.user_id.in_(list(user_ids) or [-1]))
    trades, positions = tq.order_by(StoryTrade.ts).all(), pq.all()
    sids = {t.sid for t in trades} | {p.sid for p in positions}
    nar = {n.sid: n for n in session.query(Narrative).filter(Narrative.sid.in_(list(sids) or ["-"])).all()}
    mk = {m.sid: m for m in session.query(StoryMarket).filter(StoryMarket.sid.in_(list(sids) or ["-"])).all()}

    calls = {}
    for t in trades:
        k = (t.user_id, t.sid, t.side)
        c = calls.setdefault(k, {"sid": t.sid, "side": t.side, "paid": 0.0, "closed_for": 0.0,
                                 "bought": 0.0, "sold": 0.0, "trades": 0, "first_ts": t.ts,
                                 "last_ts": t.ts, "entry_score": t.score, "payout": 0.0,
                                 "held": 0.0, "status": None, "settled_at": None})
        c["trades"] += 1
        c["last_ts"] = t.ts
        if t.action == "OPEN":
            c["paid"] += t.credits
            c["bought"] += t.units
        else:
            c["closed_for"] += t.credits
            c["sold"] += t.units
    for p in positions:
        c = calls.get((p.user_id, p.sid, p.side))
        if not c:
            continue
        if p.status == "OPEN":
            c["held"] += p.units
        elif p.status in ("SETTLED", "REFUNDED"):
            c["payout"] += p.payout or 0
            c["status"] = p.status
            c["settled_at"] = max(c["settled_at"] or 0, p.settled_at or 0)

    out = defaultdict(list)
    for (uid, sid, side), c in calls.items():
        n, m = nar.get(sid), mk.get(sid)
        open_val = close_value(base_for(n) if n else 0, m.crowd_offset if m else 0, side, c["held"]) if c["held"] else 0.0
        finished = c["held"] <= 0
        status = "OPEN" if not finished else (c["status"] or "CLOSED")
        end_ts = (c["settled_at"] or c["last_ts"]) if finished else None
        profit = c["closed_for"] + c["payout"] + open_val - c["paid"]
        out[uid].append({
            "sid": sid, "title": n.title if n else sid, "side": side, "status": status,
            "story_status": n.status if n else None,
            "bought": c["bought"], "sold": c["sold"], "held": c["held"],
            "avg_price": round(c["paid"] / c["bought"], 2) if c["bought"] else None,
            "paid": round(c["paid"], 2), "closed_for": round(c["closed_for"], 2),
            "payout": round(c["payout"], 2), "open_value": round(open_val, 2),
            "profit": round(profit, 2), "return_pct": round(100 * profit / c["paid"], 1) if c["paid"] else None,
            "entry_score": c["entry_score"], "final": n.final_score if n else None,
            "first_ts": c["first_ts"], "end_ts": end_ts, "trades": c["trades"],
        })
    for uid in out:
        out[uid].sort(key=lambda x: (x["status"] != "OPEN", -(x["end_ts"] or x["first_ts"])))
    return out


def _summary(calls, now):
    done = [c for c in calls if c["status"] != "OPEN" and c["status"] != "REFUNDED"]
    wins = [c for c in done if c["profit"] > 0]
    week = [c for c in done if c["end_ts"] and c["end_ts"] >= now - WEEK_S]
    best = max(done, key=lambda c: c["profit"], default=None)
    return {
        "profit": round(sum(c["profit"] for c in calls), 2),
        "profit_week": round(sum(c["profit"] for c in week), 2),
        "stories": len({c["sid"] for c in calls}),
        "calls": len(calls), "finished": len(done), "open": sum(1 for c in calls if c["status"] == "OPEN"),
        "wins": len(wins), "win_rate": round(100 * len(wins) / len(done)) if done else None,
        "week_calls": len(week),
        "best": ({"sid": best["sid"], "title": best["title"], "side": best["side"],
                  "profit": best["profit"]} if best and best["profit"] > 0 else None),
        "invested": round(sum(c["paid"] for c in calls), 2),
    }


def user_history(session, user_id):
    now = time.time()
    calls = _calls(session, [user_id]).get(user_id, [])
    seats = founding_numbers(session)
    return {"calls": calls, "summary": _summary(calls, now), "founding": seats.get(user_id),
            "seats": FOUNDING_SEATS, "seats_taken": len(seats)}


def leaderboard(session, period="all", name_of=lambda uid: str(uid), limit=100):
    now = time.time()
    mods = _moderators()
    seats = founding_numbers(session)
    rows = []
    for uid, calls in _calls(session).items():
        if uid in mods:
            continue
        s = _summary(calls, now)
        if period == "week" and not s["week_calls"]:
            continue
        rows.append({"user_id": str(uid), "name": name_of(uid), "founding": seats.get(uid), **s})
    key = "profit_week" if period == "week" else "profit"
    rows.sort(key=lambda r: (r[key], r["win_rate"] or 0, -r["stories"]), reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {"period": period, "rows": rows[:limit], "traders": len(rows),
            "seats": FOUNDING_SEATS, "seats_taken": len(seats), "now": now}
