"""
YouTube comments — test phase (21 Nov 2026)
===========================================
Much of the arguing about a food brand happens in the comments under its videos. With the YouTube key
we already have, we read up to 100 top comments under:
  • big brand videos found by the YouTube discovery (attention layer), and
  • the most-viewed videos of each story that counts in the index.
For each video: comments read, how many complain (same complaint words as the Bluesky buzz), how many
joke. At most PER_RUN videos every EVERY_S, each video again after RECHECK_H. One comment call costs 1
quota unit, so this adds well under 100 units a day.

Recorded only: the share of complaining comments is stored on story warnings ('comment_complaints'),
and the track record shows whether angry comment sections come before damage.
"""

import json
import re
import time

from db.models import AttentionItem, SocialSignal, VideoComments

PER_RUN = 4
EVERY_S = 2 * 3600
RECHECK_H = 24
ANGRY_SHARE = 0.3
_st = {"last": 0.0}
_ID_RX = re.compile(r"[?&]v=([\w-]{6,20})")


def _video_ids_for_stories(session, rows):
    """{video_id: (sid, brand)} from the latest YouTube check of each counting story."""
    out = {}
    counting = {r["sid"]: r for r in rows if (r.get("weighted") or 0) > 0}
    if not counting:
        return out
    for s in (session.query(SocialSignal).filter(SocialSignal.sid.in_(list(counting)), SocialSignal.source == "youtube",
                                                 SocialSignal.status == "OK").order_by(SocialSignal.ts).all()):
        try:
            for v in (json.loads(s.sample or "[]") or [])[:2]:
                m = _ID_RX.search(v.get("url") or "")
                if m:
                    out[m.group(1)] = (s.sid, counting[s.sid].get("company"), v.get("text"))
        except Exception:
            pass
    return out


def read(video_id):
    """Up to 100 top-level comments, most relevant first → list of texts."""
    from engine import youtube as Y
    d = Y._get("commentThreads", {"part": "snippet", "videoId": video_id, "maxResults": 100,
                                  "order": "relevance", "textFormat": "plainText"})
    out = []
    for it in d.get("items", []):
        sn = ((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
        if sn.get("textDisplay"):
            out.append(sn["textDisplay"][:400])
    return out


def score(texts):
    from engine.discovery import COMPLAINT_RX
    from engine.attention import RIDICULE_RX
    laugh = re.compile(r"\blol\b|\blmao\b|\bhaha|😂|🤣|💀", re.I)
    comp = [t for t in texts if COMPLAINT_RX.search(t)]
    jokes = sum(1 for t in texts if RIDICULE_RX.search(t) or laugh.search(t))
    return {"comments": len(texts), "complaints": len(comp), "jokes": jokes, "sample": comp[:3]}


def run(session, rows):
    """Read comments for a few due videos (recorded only). Called each index reading; runs every 2 h."""
    from engine import youtube as Y
    if not Y.configured() or time.time() < Y._state.get("blocked_until", 0) or time.time() - _st["last"] < EVERY_S:
        return 0
    if Y._units_today(session) + 100 > Y.DAILY_UNITS:
        return 0
    _st["last"] = time.time()
    todo = _video_ids_for_stories(session, rows)
    for a in (session.query(AttentionItem).filter(AttentionItem.kind == "video", AttentionItem.ts >= time.time() - 3 * 86400)
              .order_by(AttentionItem.size.desc()).limit(6).all()):
        vid = (a.key or "")[3:]
        if vid and vid not in todo:
            todo[vid] = (None, a.brand, a.title)
    done = 0
    for vid, (sid, brand, title) in todo.items():
        if done >= PER_RUN:
            break
        last = (session.query(VideoComments.ts).filter(VideoComments.video_id == vid)
                .order_by(VideoComments.ts.desc()).first())
        if last and time.time() - last[0] < RECHECK_H * 3600:
            continue
        rec = VideoComments(video_id=vid, sid=sid, brand=(brand or "")[:60] or None, ts=time.time(), title=(title or "")[:300])
        try:
            sc = score(read(vid))
            rec.comments, rec.complaints, rec.jokes, rec.sample = sc["comments"], sc["complaints"], sc["jokes"], json.dumps(sc["sample"])
        except Exception as e:
            rec.status = "ERROR"
            if "commentsDisabled" not in str(e):
                print(f"⚠️ youtube comments {vid}: {e}")
        session.add(rec)
        done += 1
    session.commit()
    if done:
        print(f"💬 youtube comments: {done} videos read")
    return done


def attach(session, rows):
    """r['comment_complaints'] = share of complaining comments under the story's videos (latest reads)."""
    sids = [r["sid"] for r in rows] or ["-"]
    agg = {}
    for v in session.query(VideoComments).filter(VideoComments.sid.in_(sids), VideoComments.status == "OK",
                                                 VideoComments.ts >= time.time() - 3 * 86400).all():
        a = agg.setdefault(v.sid, [0, 0])
        a[0] += v.comments or 0
        a[1] += v.complaints or 0
    for r in rows:
        a = agg.get(r["sid"])
        r["comment_complaints"] = round(a[1] / a[0], 2) if a and a[0] >= 20 else None
    return rows


def for_story(session, sid):
    vs = (session.query(VideoComments).filter(VideoComments.sid == sid, VideoComments.status == "OK")
          .order_by(VideoComments.ts.desc()).limit(4).all())
    if not vs:
        return None
    n, c, j = sum(v.comments or 0 for v in vs), sum(v.complaints or 0 for v in vs), sum(v.jokes or 0 for v in vs)
    sample = []
    for v in vs:
        sample += json.loads(v.sample or "[]")
    return {"videos": len(vs), "comments": n, "complaints": c, "jokes": j, "sample": sample[:3], "ts": vs[0].ts}


def for_videos(session, video_ids):
    out = {}
    for v in (session.query(VideoComments).filter(VideoComments.video_id.in_(list(video_ids) or ["-"]),
                                                  VideoComments.status == "OK").order_by(VideoComments.ts).all()):
        out[v.video_id] = {"comments": v.comments, "complaints": v.complaints, "jokes": v.jokes}
    return out
