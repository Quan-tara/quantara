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

import re
import time
from urllib.parse import urlparse

from db.database import SessionLocal
from db.models import (Narrative, StoryComment, StoryCommentBlock, StoryCommentReport,
                       StoryCommentVote, StoryPosition)

# Evidence: a comment that argues for one outcome (YES / NO), ideally with a source link.
# A "counter" reply automatically argues for the other side. Others mark evidence "useful".
# When the story settles, evidence on the winning side is marked "called it" (WIN) with how early
# it came, and the most useful winning evidence earns CALL_BONUS credits.
KINDS      = ("YES", "NO")
CALL_BONUS = 50.0

MAX_LEN     = 600
COOLDOWN_S  = 15
REPORT_HIDE = 3
VERDICTS    = ("REAL", "EXAGGERATED", "HYPE")


class CommentError(Exception):
    pass


def is_moderator(user_id):
    from engine.constants import ADMIN_ID, MM_USER_ID
    return user_id in (ADMIN_ID, MM_USER_ID)


URL_RX = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|dclid|msclkid|igshid|mc_cid|mc_eid|ref|ref_src|si|s|t|smid|cmpid)$", re.I)


def _strip_tracking(link):
    """Drop tracking bits (?utm_source=…, fbclid…) so links stay short and honest."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    try:
        u = urlsplit(link)
        q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not _TRACKING.match(k)]
        return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), ""))
    except Exception:
        return link


def _clean_link(link):
    link = (link or "").strip().rstrip(".,;:!?")
    if not link:
        return None
    if len(link) > 500 or not re.match(r"^https?://[^\s/$.?#][^\s]*$", link, re.I):
        raise CommentError("The link must be a full web address (https://…)")
    return _strip_tracking(link)


def _link_from_body(body):
    """No link given? Take the first web address written in the text, and take it out of the text."""
    m = URL_RX.search(body or "")
    if not m:
        return body, None
    url = m.group(0).rstrip(".,;:!?")
    rest = (body[:m.start()] + body[m.start() + len(url):]).strip()
    rest = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", rest))
    return (rest or body), url


def domain(link):
    try:
        d = urlparse(link).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


def post(user_id, sid, body, verdict=None, parent_id=None, kind=None, link=None):
    """kind: 'YES' / 'NO' for evidence, 'COUNTER' on a reply to evidence (takes the other side)."""
    body = (body or "").strip()
    if not (link or "").strip():
        body, link = _link_from_body(body)
    link = _clean_link(link)
    kind = (kind or "").upper() or None
    if kind and kind not in KINDS + ("COUNTER",):
        raise CommentError("Evidence must be for YES or NO")
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
            if kind == "COUNTER":
                if par.kind not in KINDS:
                    raise CommentError("Counter-arguments answer evidence")
                kind = "NO" if par.kind == "YES" else "YES"
            parent_id = par.parent_id or par.id          # keep replies one level deep
            verdict = None                               # verdicts only on top-level comments
        elif kind == "COUNTER":
            raise CommentError("A counter-argument must reply to evidence")
        pos = (s.query(StoryPosition).filter(StoryPosition.user_id == user_id, StoryPosition.sid == sid,
                                             StoryPosition.status == "OPEN", StoryPosition.units > 0).first())
        c = StoryComment(sid=sid, user_id=user_id, parent_id=parent_id, body=body, verdict=verdict,
                         side=pos.side if pos else None, units=pos.units if pos else None, ts=now,
                         kind=kind, link=link, useful=0)
        s.add(c)
        s.commit()
        return c.id
    finally:
        s.close()


def vote_useful(user_id, comment_id):
    """Mark evidence as useful (toggle). Returns the new count."""
    s = SessionLocal()
    try:
        c = s.get(StoryComment, int(comment_id))
        if not c or c.deleted:
            raise CommentError("Comment not found")
        if c.kind not in KINDS:
            raise CommentError("Only evidence can be marked useful")
        if c.user_id == user_id:
            raise CommentError("You can't vote for your own evidence")
        v = (s.query(StoryCommentVote).filter(StoryCommentVote.comment_id == c.id,
                                              StoryCommentVote.user_id == user_id).first())
        if v:
            s.delete(v)
            c.useful = max(0, (c.useful or 0) - 1)
        else:
            s.add(StoryCommentVote(comment_id=c.id, user_id=user_id, ts=time.time()))
            c.useful = (c.useful or 0) + 1
        s.commit()
        return c.useful
    finally:
        s.close()


def call_it(session, sid, winner, settled_at):
    """Story settled with `winner` ('YES'/'NO'): mark every piece of evidence WIN or LOSE, and pay
    CALL_BONUS to the author of the most useful winning evidence (at least one vote, earliest wins ties).
    Uses the caller's session; the caller commits."""
    rows = (session.query(StoryComment).filter(StoryComment.sid == sid, StoryComment.kind.in_(KINDS),
                                               StoryComment.deleted.is_(False)).all())
    for c in rows:
        c.called = "WIN" if c.kind == winner else "LOSE"
        c.lead_s = max(0.0, settled_at - c.ts)
    best = sorted([c for c in rows if c.called == "WIN" and (c.useful or 0) >= 1 and (c.reports or 0) < REPORT_HIDE],
                  key=lambda c: (-(c.useful or 0), c.ts))
    if best and not any(c.bonus for c in rows):
        from engine.story_market import account
        best[0].bonus = CALL_BONUS
        account(session, best[0].user_id).credits += CALL_BONUS
    return len(rows)


def called_by(user_id, limit=20):
    """A player's evidence that turned out right — for the Me page."""
    s = SessionLocal()
    try:
        rows = (s.query(StoryComment, Narrative.title).join(Narrative, Narrative.sid == StoryComment.sid)
                .filter(StoryComment.user_id == user_id, StoryComment.called == "WIN", StoryComment.deleted.is_(False))
                .order_by(StoryComment.ts.desc()).limit(limit).all())
        total = (s.query(StoryComment).filter(StoryComment.user_id == user_id, StoryComment.called.isnot(None),
                                              StoryComment.deleted.is_(False)).count())
        return {"wins": [{"id": c.id, "sid": c.sid, "title": t, "kind": c.kind, "body": c.body[:160],
                          "link": c.link, "useful": c.useful or 0, "lead_s": c.lead_s, "bonus": c.bonus}
                         for c, t in rows],
                "settled": total}
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
        mine_reported, mine_voted = set(), set()
        if viewer_id is not None:
            mine_reported = {r.comment_id for r in s.query(StoryCommentReport)
                             .filter(StoryCommentReport.user_id == viewer_id).all()}
            mine_voted = {v.comment_id for v in s.query(StoryCommentVote)
                          .filter(StoryCommentVote.user_id == viewer_id).all()}
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
                 "reported": c.id in mine_reported, "replies": [],
                 "kind": c.kind, "useful": c.useful or 0, "voted": c.id in mine_voted,
                 "called": c.called, "lead_s": c.lead_s, "bonus": c.bonus}
            body, link = c.body, c.link
            if not link:                                 # older posts: link written inside the text
                try:
                    body, raw = _link_from_body(c.body)
                    link = _clean_link(raw) if raw else None
                except Exception:
                    body, link = c.body, None
            d["body"] = None if (hidden and not mod) else body
            d["link"] = None if (hidden and not mod) else link
            d["domain"] = domain(c.link) if d["link"] else None
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
        # strongest case per side: most useful visible evidence (top-level or counter), earliest wins ties
        ev = [x for t in top for x in [t] + t["replies"] if x["kind"] in KINDS and x["body"] and not x["held"]]
        strongest = {}
        for k in KINDS:
            side = sorted([x for x in ev if x["kind"] == k], key=lambda x: (-x["useful"], x["ts"]))
            strongest[k] = {k_: side[0][k_] for k_ in ("id", "name", "body", "link", "domain", "useful", "ts", "called")} if side else None
        return {"comments": top, "tally": tally, "count": sum(1 + len(t["replies"]) for t in top),
                "strongest": strongest, "evidence": {k: sum(1 for x in ev if x["kind"] == k) for k in KINDS},
                "moderator": mod, "blocked": blocked_me,
                "rules": {"max_len": MAX_LEN, "cooldown_s": COOLDOWN_S, "report_hide": REPORT_HIDE}}
    finally:
        s.close()
