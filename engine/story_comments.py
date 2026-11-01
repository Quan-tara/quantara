"""
Story comments
==============
People discuss each story on its page: is it real, exaggerated or hype?
  • a comment can carry a verdict (REAL / EXAGGERATED / HYPE) — the page shows the tally
  • replies are one level deep
  • the commenter's position on the story (GROW/FADE, units) is shown next to the comment
Moderation: length limit, one comment per COOLDOWN_S per user, report button (comments
with REPORT_HIDE reports are hidden until a moderator looks), moderator delete, block user.

Comments are never an input to the story score, the FSNI or the payout.
NOTE: login is not secure yet (user id from the browser) — add proper sessions before launch.
"""

import time

from db.database import SessionLocal
from db.models import (Narrative, StoryComment, StoryCommentBlock, StoryCommentReport,
                       StoryPosition)

MAX_LEN     = 600
COOLDOWN_S  = 15
REPORT_HIDE = 3
VERDICTS    = ("REAL", "EXAGGERATED", "HYPE")


class CommentError(Exception):
    pass


def is_moderator(user_id):
    from engine.constants import ADMIN_ID, MM_USER_ID
    return user_id in (ADMIN_ID, MM_USER_ID)


def post(user_id, sid, body, verdict=None, parent_id=None):
    body = (body or "").strip()
    if not body:
        raise CommentError("Write something first")
    if len(body) > MAX_LEN:
        raise CommentError(f"Max {MAX_LEN} characters ({len(body)} now)")
    verdict = (verdict or "").upper() or None
    if verdict and verdict not in VERDICTS:
        raise CommentError("Verdict must be real, exaggerated or hype")
    s = SessionLocal()
    try:
        now = time.time()
        if not s.query(Narrative).filter(Narrative.sid == sid).first():
            raise CommentError("Comments open once a story is listed")
        if s.get(StoryCommentBlock, user_id):
            raise CommentError("You can't comment right now")
        last = (s.query(StoryComment).filter(StoryComment.user_id == user_id)
                .order_by(StoryComment.ts.desc()).first())
        if last and now - last.ts < COOLDOWN_S and not is_moderator(user_id):
            raise CommentError(f"Please wait {int(COOLDOWN_S - (now - last.ts)) + 1}s before posting again")
        if parent_id:
            par = s.get(StoryComment, int(parent_id))
            if not par or par.sid != sid or par.deleted:
                raise CommentError("The comment you reply to is gone")
            parent_id = par.parent_id or par.id          # keep replies one level deep
            verdict = None                               # verdicts only on top-level comments
        pos = (s.query(StoryPosition).filter(StoryPosition.user_id == user_id, StoryPosition.sid == sid,
                                             StoryPosition.status == "OPEN", StoryPosition.units > 0).first())
        c = StoryComment(sid=sid, user_id=user_id, parent_id=parent_id, body=body, verdict=verdict,
                         side=pos.side if pos else None, units=pos.units if pos else None, ts=now)
        s.add(c)
        s.commit()
        return c.id
    finally:
        s.close()


def report(user_id, comment_id, reason=None):
    s = SessionLocal()
    try:
        c = s.get(StoryComment, int(comment_id))
        if not c or c.deleted:
            raise CommentError("Comment not found")
        if c.user_id == user_id:
            raise CommentError("You can't report your own comment")
        if s.query(StoryCommentReport).filter(StoryCommentReport.comment_id == c.id,
                                              StoryCommentReport.user_id == user_id).first():
            raise CommentError("You already reported this")
        s.add(StoryCommentReport(comment_id=c.id, user_id=user_id, reason=(reason or "")[:200], ts=time.time()))
        c.reports += 1
        s.commit()
        return c.reports
    finally:
        s.close()


def moderate(mod_id, comment_id, action):
    """action: delete | restore (clear reports) | block (delete + block the author) | unblock"""
    if not is_moderator(mod_id):
        raise CommentError("Moderators only")
    s = SessionLocal()
    try:
        c = s.get(StoryComment, int(comment_id))
        if not c:
            raise CommentError("Comment not found")
        if action == "delete":
            c.deleted = True
        elif action == "restore":
            c.deleted, c.reports = False, 0
        elif action == "block":
            c.deleted = True
            if not s.get(StoryCommentBlock, c.user_id):
                s.add(StoryCommentBlock(user_id=c.user_id, ts=time.time(), note=f"comment {c.id}"))
        elif action == "unblock":
            b = s.get(StoryCommentBlock, c.user_id)
            if b:
                s.delete(b)
        else:
            raise CommentError("unknown action")
        s.commit()
        return True
    finally:
        s.close()


def thread(sid, viewer_id=None, name_of=lambda uid: str(uid)):
    """Comments for a story, newest first, with replies under each, and the verdict tally."""
    mod = bool(viewer_id is not None and is_moderator(viewer_id))
    s = SessionLocal()
    try:
        rows = s.query(StoryComment).filter(StoryComment.sid == sid).order_by(StoryComment.ts).all()
        blocked = {b.user_id for b in s.query(StoryCommentBlock).all()}
        mine_reported = set()
        if viewer_id is not None:
            mine_reported = {r.comment_id for r in s.query(StoryCommentReport)
                             .filter(StoryCommentReport.user_id == viewer_id).all()}
        from engine.story_stats import founding_numbers
        seats = founding_numbers(s)
        names, tally = {}, {v: 0 for v in VERDICTS}
        voted = set()
        # latest verdict per user counts once
        for c in sorted(rows, key=lambda x: x.ts, reverse=True):
            if c.verdict and not c.deleted and c.user_id not in voted:
                tally[c.verdict] += 1
                voted.add(c.user_id)

        def view(c):
            if c.user_id not in names:
                names[c.user_id] = name_of(c.user_id)
            hidden = c.deleted or (c.reports >= REPORT_HIDE)
            d = {"id": c.id, "name": names[c.user_id], "founding": seats.get(c.user_id),
                 "ts": c.ts, "verdict": c.verdict,
                 "side": c.side, "units": c.units, "mine": viewer_id == c.user_id,
                 "deleted": c.deleted, "held": (not c.deleted) and c.reports >= REPORT_HIDE,
                 "reported": c.id in mine_reported, "replies": []}
            d["body"] = None if (hidden and not mod) else c.body
            if mod:
                d.update({"reports": c.reports, "author_blocked": c.user_id in blocked})
            return d

        top, by_id = [], {}
        for c in rows:
            if c.parent_id is None:
                by_id[c.id] = view(c)
                top.append(by_id[c.id])
        for c in rows:
            if c.parent_id is not None and c.parent_id in by_id:
                by_id[c.parent_id]["replies"].append(view(c))
        # drop deleted threads with no visible replies (for non-moderators)
        if not mod:
            top = [t for t in top if not t["deleted"] or any(not r["deleted"] for r in t["replies"])]
            for t in top:
                t["replies"] = [r for r in t["replies"] if not r["deleted"]]
        top.sort(key=lambda t: max([t["ts"]] + [r["ts"] for r in t["replies"]]), reverse=True)
        blocked_me = viewer_id is not None and viewer_id in blocked
        return {"comments": top, "tally": tally, "count": sum(1 + len(t["replies"]) for t in top),
                "moderator": mod, "blocked": blocked_me,
                "rules": {"max_len": MAX_LEN, "cooldown_s": COOLDOWN_S, "report_hide": REPORT_HIDE}}
    finally:
        s.close()
