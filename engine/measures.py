"""
Phase 2 measurements — timelines and company responses (22 Nov 2026, recorded only)
====================================================================================
See "Quantara — The Backbone Model". These only build up from the day they start, so they run now and
are analysed once 2–3 weeks of data exist.

  timeline   every spreading or listed brand story, one reading per TIMELINE_EVERY_H: outlets, new outlets in
             12 h, articles, score, Bluesky posts, YouTube videos, responses so far. Early-warning signals
             (complexity science: rising variance and autocorrelation of the growth shortly before a tip)
             are computed from it.
  response   the feedback slot: headlines in which the brand responds. Kinds, strongest first:
             reversal (backs down), apology, denial (pushes back), action (fires, suspends, settles,
             refunds…), statement (responds, says). A story with none = silence.
"""

import json
import re
import statistics
import time

from db.models import CompanyResponse, SocialSignal, StoryTimeline

TIMELINE_EVERY_H = 3

RESPONSE_RX = [
    ("reversal", re.compile(r"\b(reverses?|reversed|reversing|backtracks?|backtracked|walks? back|walked back|u-?turn|"
                            r"backs? down|backed down|caves?|caved|scraps?|scrapped|brings? (?:it )?back|restores?|restored|"
                            r"reinstates?|reinstated|rolls? back|rolled back|reverts?|reverted|drops? (?:the )?(?:plan|change|policy|fee))\b", re.I)),
    ("apology", re.compile(r"\b(apologi[sz]es?|apologi[sz]ed|apology|says sorry|sorry)\b", re.I)),
    ("denial", re.compile(r"\b(denies|denied|deny|disputes?|disputed|refutes?|refuted|rejects? (?:claims|allegations|reports)|"
                          r"pushes? back|pushed back|defends?|defended|not true|false claims?|misleading)\b", re.I)),
    ("action", re.compile(r"\b(fires?|fired|suspends?|suspended|settles?|settled|settlement|refunds?|refunded|compensat\w*|"
                          r"launches? (?:an? )?investigation|investigating|pulls?|pulled|closes? (?:the )?(?:store|location|restaurant)|"
                          r"pauses?|paused|halts?|halted)\b", re.I)),
    ("statement", re.compile(r"\b(statement|responds?|responded|response|spokes(?:person|man|woman)|addresses|addressed|"
                             r"breaks? silence|speaks out|comments? on|says it|said it)\b", re.I)),
]


def response_kind(title, brand):
    """The strongest kind of brand response in a headline about that brand, or None."""
    t = (title or "").replace("’", "'")
    b = (brand or "").lower().replace("’", "'")
    if not b or b not in t.lower():
        return None
    for kind, rx in RESPONSE_RX:
        if rx.search(t):
            return kind
    return None


def record_responses(session, sid, brand, articles, now=None):
    now = now or time.time()
    if not brand:
        return 0
    have = {t for (t,) in session.query(CompanyResponse.title).filter(CompanyResponse.sid == sid).all()}
    n = 0
    for a in articles or []:
        t = (a.get("title") or "")[:300]
        if not t or t in have:
            continue
        k = response_kind(t, brand)
        if k:
            session.add(CompanyResponse(sid=sid, brand=brand, ts=a.get("ts") or now, seen=now, kind=k, title=t,
                                        source=(a.get("source") or "")[:120], url=a.get("url")))
            have.add(t)
            n += 1
    return n


def _latest_social(session, sid, now):
    posts = videos = None
    for src, p in (session.query(SocialSignal.source, SocialSignal.posts)
                   .filter(SocialSignal.sid == sid, SocialSignal.status == "OK", SocialSignal.ts >= now - 24 * 3600)
                   .order_by(SocialSignal.ts).all()):
        if src == "youtube":
            videos = p
        else:
            posts = p
    return posts, videos


def record_point(session, sid, brand, outlets, new_12h, articles, score, listed, now=None):
    now = now or time.time()
    last = (session.query(StoryTimeline.ts).filter(StoryTimeline.sid == sid)
            .order_by(StoryTimeline.ts.desc()).first())
    if last and now - last[0] < TIMELINE_EVERY_H * 3600 - 300:
        return False
    posts, videos = _latest_social(session, sid, now)
    resp = session.query(CompanyResponse).filter(CompanyResponse.sid == sid).count()
    session.add(StoryTimeline(sid=sid, ts=now, brand=brand, outlets=outlets, new_12h=new_12h, articles=articles,
                              score=score, posts=posts, videos=videos, responses=resp, listed=bool(listed)))
    return True


def record_run(session, stories, active, now=None):
    """Called once per everyday listing run: live candidate stories (brand + real trouble) and every listed
    'Will it tip?' story, whether or not it is in this run's news."""
    from engine.story_market import new_outlets, outlets_now, _snap
    from engine.tipping import tip_gate
    now = now or time.time()
    done, seen = 0, set()
    for s in stories:
        brand = s.get("company")
        if not brand or not s.get("sid"):
            continue
        if not tip_gate(s.get("title"), [], brand)[0]:
            continue
        members = s.get("members") or []
        record_responses(session, s["sid"], brand, members, now)
        cur = int(max(s.get("outlets") or 0, len(s.get("all_outlets") or [])))
        listed = any(n.sid == s["sid"] for n in active)
        done += record_point(session, s["sid"], brand, cur, new_outlets(members, now - 12 * 3600), len(members),
                             s.get("story_score"), listed, now)
        seen.add(s["sid"])
    for n in active:
        snap = _snap(n)
        b = (snap.get("bet") or {})
        if b.get("kind") != "tip" or n.sid in seen:
            continue
        brand = b.get("company") or snap.get("lead_brand") or snap.get("company")
        arts = snap.get("articles") or []
        record_responses(session, n.sid, brand, arts, now)
        done += record_point(session, n.sid, brand, outlets_now(n), new_outlets(arts, now - 12 * 3600), len(arts),
                             n.last_score, True, now)
    return done


# ─────────────────────────────── early-warning signals ───────────────────────────────
def early_warning(points, window=6):
    """Complexity science: shortly before a system tips, its fluctuations grow (variance) and slow down
    (lag-1 autocorrelation). Computed on the growth between readings (new outlets per reading), over the
    last `window` readings against the `window` before. None until 2 × window readings exist."""
    xs = [p["outlets"] for p in points if p.get("outlets") is not None]
    d = [b - a for a, b in zip(xs, xs[1:])]
    if len(d) < 2 * window:
        return None

    def ac1(v):
        if len(v) < 3 or statistics.pvariance(v) == 0:
            return 0.0
        m = statistics.mean(v)
        num = sum((v[i] - m) * (v[i - 1] - m) for i in range(1, len(v)))
        return num / sum((x - m) ** 2 for x in v)
    now_, before = d[-window:], d[-2 * window:-window]
    return {"variance": round(statistics.pvariance(now_), 3), "variance_before": round(statistics.pvariance(before), 3),
            "autocorr": round(ac1(now_), 3), "autocorr_before": round(ac1(before), 3),
            "rising": statistics.pvariance(now_) > statistics.pvariance(before) and ac1(now_) > ac1(before)}


def for_story(session, sid):
    from db.models import StoryBridges
    pts = [{"ts": p.ts, "outlets": p.outlets, "new_12h": p.new_12h, "posts": p.posts, "videos": p.videos,
            "responses": p.responses, "listed": p.listed}
           for p in session.query(StoryTimeline).filter(StoryTimeline.sid == sid).order_by(StoryTimeline.ts).all()]
    resp = [{"ts": r.ts, "kind": r.kind, "title": r.title, "source": r.source, "url": r.url}
            for r in session.query(CompanyResponse).filter(CompanyResponse.sid == sid).order_by(CompanyResponse.ts).all()]
    br = [{"ts": b.ts, "posts": b.posts, "authors": b.authors, "sampled": b.sampled, "clusters": b.clusters,
           "largest_share": b.largest_share, "audience": b.audience, "big_accounts": b.big_accounts}
          for b in session.query(StoryBridges).filter(StoryBridges.sid == sid, StoryBridges.status == "OK")
          .order_by(StoryBridges.ts).all()]
    first = pts[0]["ts"] if pts else None
    return {"timeline": pts, "early_warning": early_warning(pts), "responses": resp,
            "first_response_h": round((resp[0]["ts"] - first) / 3600, 1) if resp and first else None,
            "bridges": br}
