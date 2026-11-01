"""
Story listing
=============
A story becomes LISTED when, in a collector run,
    story score >= LIST_SCORE   and   outlets >= LIST_MIN_OUTLETS.
It stays listed for LIST_WINDOW_H hours (72 h), then CLOSES with a final score —
the number grow/fade positions will settle on in the next step.

While listed the score is recomputed every run:
  • story still in the news  → score from this run's articles (story_score)
  • story dropped out        → same formula on the stored article list, so the
                               score fades with freshness instead of jumping to 0
Every run adds a point to the story's score history (narrative_points).
A story is listed at most once (same sid never relists).

Only outside news sources feed this. Nothing users do on the site is an input.
"""

import json
import time

import sqlalchemy as sa

from db.models import Narrative, NarrativePoint

LIST_SCORE       = 30.0
LIST_MIN_OUTLETS = 4
LIST_WINDOW_H    = 72
BACKFILL_H       = 72      # history copied from earlier readings when a story lists
MAX_ARTICLES     = 60


def _snapshot(s, old=None):
    """What we keep about a story so its page and its score survive after it leaves the news."""
    arts = {(a["title"], a["source"]): a for a in (old or {}).get("articles", [])}
    for a in s.get("members", []):
        arts[(a["title"], a["source"])] = a
    arts = sorted(arts.values(), key=lambda a: a["ts"], reverse=True)[:MAX_ARTICLES]
    outlets = sorted(set((old or {}).get("outlet_names", [])) | set(s.get("all_outlets") or []))
    return {
        "outlets": max(len(outlets), s.get("outlets", 0)),
        "outlet_names": outlets,
        "articles": arts,
        "regions": sorted(set((old or {}).get("regions", [])) | set(s.get("regions") or [])),
        "categories": sorted(set((old or {}).get("categories", [])) | set(s.get("categories") or [])),
        "severity": max(float((old or {}).get("severity", 0)), float(s.get("severity") or 0)),
        "tokens": list(s.get("tokens") or (old or {}).get("tokens", []))[:40],
        "hazards": list(s.get("hazards") or (old or {}).get("hazards", [])),
        "ts": max(float((old or {}).get("ts", 0)), float(s.get("ts") or 0)),
        "first_ts": min(x for x in [(old or {}).get("first_ts"), s.get("first_ts")] if x) if (s.get("first_ts") or (old or {}).get("first_ts")) else None,
    }


def _score_from_snapshot(snap, now):
    """Score of a listed story that isn't in this run's news: same formula, stored articles."""
    from engine.narrative_index import story_score
    recent = sum(1 for a in snap.get("articles", []) if a["ts"] >= now - 12 * 3600)
    return story_score({"outlets": snap.get("outlets", 0), "regions": snap.get("regions", []),
                        "severity": snap.get("severity", 0), "recent_12h": recent,
                        "ts": snap.get("ts") or now}, now)


def history_from_readings(session, sid, since_ts):
    """Score points for a story from the per-reading story_index (before it was listed)."""
    rows = session.execute(sa.text(
        "SELECT ts, headlines FROM index_ticks WHERE ts >= :a AND headlines LIKE :pat ORDER BY ts"
    ), {"a": since_ts, "pat": f'%"sid": "{sid}"%'}).fetchall()
    out = []
    for ts, h in rows:
        try:
            for x in (json.loads(h) or {}).get("story_index") or []:
                if x.get("sid") == sid:
                    out.append((float(ts), float(x.get("score") or 0)))
                    break
        except Exception:
            continue
    return out


def listed_for_matching(session):
    """LISTED stories in the shape assign_story_ids matches against."""
    out = []
    for n in session.query(Narrative).filter(Narrative.status == "LISTED").all():
        snap = json.loads(n.snapshot or "{}")
        out.append({"sid": n.sid, "first_seen": n.first_seen, "score": n.last_score,
                    "tokens": snap.get("tokens", []), "hazards": snap.get("hazards", [])})
    return out


def update_listings(stories, session, now=None):
    """Call once per collector run, after assign_story_ids. Marks stories with
    listed / closes_at and keeps the narratives tables up to date."""
    now = now or time.time()
    live = {s["sid"]: s for s in stories if s.get("sid")}
    known = {n.sid: n for n in session.query(Narrative).filter(Narrative.sid.in_(list(live) or ["-"])).all()}
    active = session.query(Narrative).filter(Narrative.status == "LISTED").all()
    for n in active:
        known.setdefault(n.sid, n)

    # 1) update / close what is already listed
    for n in active:
        snap = json.loads(n.snapshot or "{}")
        s = live.get(n.sid)
        if s:
            snap = _snapshot(s, snap)
            score = s["story_score"]
            n.title, n.url, n.source = s["title"], s["url"], s["source"]
            n.last_seen = now
        else:
            score = _score_from_snapshot(snap, now)
        n.snapshot = json.dumps(snap)
        n.last_score = score
        n.peak_score = max(n.peak_score or 0, score)
        session.add(NarrativePoint(sid=n.sid, ts=now, score=score))
        if now >= n.closes_at:
            n.status, n.final_score, n.closed_at = "CLOSED", score, now
            print(f"🔒 Story closed  {n.sid}  final {score:.1f}  {n.title[:70]}")

    # 2) list new stories that crossed the threshold
    for s in stories:
        if s["sid"] in known:
            continue
        if s["story_score"] < LIST_SCORE or s["outlets"] < LIST_MIN_OUTLETS:
            continue
        n = Narrative(sid=s["sid"], title=s["title"], url=s["url"], source=s["source"],
                      status="LISTED", first_seen=s.get("first_seen"), listed_at=now,
                      closes_at=now + LIST_WINDOW_H * 3600, list_score=s["story_score"],
                      last_score=s["story_score"], peak_score=s["story_score"],
                      last_seen=now, snapshot=json.dumps(_snapshot(s)))
        session.add(n)
        for ts, sc in history_from_readings(session, s["sid"], now - BACKFILL_H * 3600):
            session.add(NarrativePoint(sid=s["sid"], ts=ts, score=sc))
        session.add(NarrativePoint(sid=s["sid"], ts=now, score=s["story_score"]))
        known[s["sid"]] = n
        print(f"📌 Story listed  {s['sid']}  score {s['story_score']:.1f}  "
              f"[{s['outlets']} outlets]  {s['title'][:70]}")

    session.commit()

    for s in stories:
        n = known.get(s["sid"])
        s["listed"] = bool(n and n.status == "LISTED")
        s["list_status"] = n.status if n else None
        s["closes_at"] = n.closes_at if n else None
