"""
"Would you still buy from <brand>?" — one tap per story
=======================================================
Every listed story about a company asks players this one question (Yes / No; an answer can be
changed). The share of NO answers is the most direct thing the platform measures about the chain
    story → people talk → people change what they buy → revenue falls
It feeds the outlook ("intent") and, per brand, the company page. Recorded only: it never
moves a story's score or a payout.
"""

import json
import time

from db.models import Narrative, StoryIntent

MIN_ANSWERS = 5          # the outlook uses intent only once this many answers exist
WINDOW_D = 3


def answer(session, sid, user_id, still_buy):
    n = session.query(Narrative).filter(Narrative.sid == sid).first()
    if not n:
        raise LookupError("Story not found")
    company = (json.loads(n.snapshot or "{}") or {}).get("company")
    if not company:
        raise ValueError("This story isn't about a company")
    r = session.query(StoryIntent).filter(StoryIntent.sid == sid, StoryIntent.user_id == user_id).first()
    if r:
        r.still_buy, r.ts = bool(still_buy), time.time()
    else:
        session.add(StoryIntent(sid=sid, user_id=user_id, still_buy=bool(still_buy), company=company[:60],
                                ts=time.time()))
    session.commit()
    return tally(session, sid, user_id)


def tally(session, sid, user_id=None):
    rows = session.query(StoryIntent).filter(StoryIntent.sid == sid).all()
    yes = sum(1 for r in rows if r.still_buy)
    no = len(rows) - yes
    mine = next((r.still_buy for r in rows if user_id is not None and r.user_id == user_id), None)
    return {"yes": yes, "no": no, "total": len(rows),
            "no_pct": round(100 * no / len(rows)) if rows else None, "mine": mine}


def tallies(session, sids):
    out = {}
    for r in session.query(StoryIntent).filter(StoryIntent.sid.in_(list(sids) or ["-"])).all():
        t = out.setdefault(r.sid, {"yes": 0, "no": 0})
        t["yes" if r.still_buy else "no"] += 1
    for t in out.values():
        t["total"] = t["yes"] + t["no"]
        t["no_pct"] = round(100 * t["no"] / t["total"])
    return out


def outlook_part(session):
    """Share of 'No, I wouldn't buy' over the last few days (None until MIN_ANSWERS)."""
    rows = session.query(StoryIntent).filter(StoryIntent.ts >= time.time() - WINDOW_D * 86400).all()
    if len(rows) < MIN_ANSWERS:
        return None, {"answers": len(rows), "needed": MIN_ANSWERS}
    no = sum(1 for r in rows if not r.still_buy)
    brands = {}
    for r in rows:
        b = brands.setdefault(r.company or "?", [0, 0])
        b[0] += 1
        b[1] += 0 if r.still_buy else 1
    worst = sorted(((c, round(100 * x[1] / x[0])) for c, x in brands.items() if x[0] >= 3), key=lambda x: -x[1])[:3]
    return 100.0 * no / len(rows), {"answers": len(rows), "no": no, "worst": worst}


def for_brand(session, brand):
    b = (brand or "").strip().lower().replace("’", "'")
    if not b:
        return None
    rows = [r for r in session.query(StoryIntent).filter(StoryIntent.ts >= time.time() - 30 * 86400).all()
            if b in (r.company or "").lower().replace("’", "'")]
    if not rows:
        return None
    no = sum(1 for r in rows if not r.still_buy)
    return {"answers": len(rows), "no": no, "no_pct": round(100 * no / len(rows))}
