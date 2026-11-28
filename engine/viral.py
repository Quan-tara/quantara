"""
"🔥 Will it go viral?" — everyday brand stories for the game (28 Nov 2026).

The index only counts real pressure on brands, so the board was quiet. People mostly talk about something
else: a customer's video from the drive-thru, a barista explaining a change, a worker's reveal, a creator's
review that splits the comments. Those stories are listed here as a lighter bet:

  * they come from the same news run (Newsweek / Daily Dot / BuzzFeed / The Takeout … write TikTok moments up),
  * a national food brand is named, the headline has a people / social marker, it has 2+ outlets and is fresh,
  * they are NOT counted in the index (they fail its pressure test — that is why they are here),
  * the bet is the normal tip bet, smaller and shorter: +VIRAL_GROWTH outlets (at least VIRAL_MIN) with people
    talking beyond the news, or the Bluesky talk doubling, within VIRAL_DAYS days.
"""
import json
import re
import time

VIRAL_DAILY_CAP = 4        # viral stories a day (on top of the news cap)
VIRAL_MIN_OUTLETS = 2      # spread it needs before it is listed
VIRAL_FRESH_H = 24         # newest article at most this old
VIRAL_GROWTH = 2
VIRAL_MIN = 4
VIRAL_DAYS = 3

# 28 Nov 2026 (v2): a viral story needs a PERSON and a REACTION in the headline — "Here's what to order from
# McDonald's to make breakfast tacos" (a how-to) got through v1 on the word "order".
PERSON_RX = re.compile(
    r"\btiktok(?:er|ers)?\b|\binfluencers?\b|\bcreators?\b|\byoutubers?\b|\breddit(?:or|ors)?\b|\bcustomers?\b|"
    r"\bshoppers?\b|\bdiners?\b|\bworkers?\b|\bemployees?\b|\bbaristas?\b|\bmanager\b|\bcashier\b|\bdriver\b|"
    r"\bmom\b|\bmother\b|\bdad\b|\bfather\b|\bwoman\b|\bman\b|\bcouple\b|\bteen\w*|\bfamily\b|\bkids?\b|"
    r"\bgrandma\b|\bguy\b|\bgirl\b|\bstudent\b|\bnurse\b|\bvet(?:eran)?\b|\bfans\b|\binternet\b|\bcommenters\b", re.I)
PEOPLE_RX = re.compile(
    r"\btiktok\w*|\bviral\b|\bvideo\b|\binfluencers?\b|\bcreators?\b|\byoutubers?\b|\breddit\w*|\binternet\b|"
    r"\bcommenters?\b|\bcustomers?\b|\bshoppers?\b|\bdiners?\b|\bworkers?\b|\bemployees?\b|\bbaristas?\b|\bmanager\b|"
    r"\bcashier\b|\bdrive[- ]thru\b|\breveals?\b|\bexplains?\b|\bconfess\w*|\bshows? (?:what|how|why)|\bsparks?\b|"
    r"\bdebate\b|\bdivides?\b|\bstuns?\b|\bshocks?\b|\bhack\b|\breceipt\b|\border\b|\bfans?\b|\bpeople\b|\bmom\b|"
    r"\bdad\b|\bwoman\b|\bman\b|\bcouple\b|\bteen\b", re.I)
SKIP_RX = re.compile(
    r"\bopen on\b|\bhours\b|\bclosed on\b|\bis .* open\b|\bdeals?\b|\bcoupons?\b|\bbogo\b|\bfree (?:\w+ )?(?:day|friday|for)\b|"
    r"\brecipes?\b|\bhow to\b|\bcopycat\b|\branked\b|\branking\b|\bbest\b|\b\d+ (?:things|items|ways|reasons|snacks|foods)\b|"
    r"\bstock\b|\bshares\b|\bearnings\b|\banalysts?\b|\bnasdaq\b|\bnyse\b|\bprice target\b|\bceo\b|\bopens? (?:its|a|new)\b|"
    r"\bgrand opening\b|\bobituar\w*|\bdies\b|\bdied\b|\bshooting\b|\bshot\b|\bkilled\b|\barrested\b|\bcharged\b|\bpolice\b|"
    r"\brecall\w*\b|\boutbreak\b|\bsalmonella\b|\blisteria\b|\bwhat to order\b|\bhere'?s (?:what|how|why)\b|"
    r"\bways to\b|\btips?\b|\bguide\b|\bhacks?\b|\bsecret menu\b|\bmake (?:your own|breakfast|a)\b|\bat home\b", re.I)
REACT_RX = re.compile(
    r"\bbacklash|\boutrage|\bfurious|\bangry|\bslam\w*|\bdisgust\w*|\bgross\b|\bweird\b|\bwild\b|\bshock\w*|\bstun\w*|"
    r"\bdebate|\bdivide\w*|\bsplit\b|\bcontrovers\w*|\bcalls? out|\bexpos\w*|\breveal\w*|\bconfess\w*|\bcomplain\w*|"
    r"\bcan'?t believe|\bhilarious|\broast\w*|\bmock\w*|\btroll\w*|\bpetty|\bscam\w*|\bripoff|\bdrama\b|"
    r"\bargu\w*|\bfight\w*|\bclash\w*|\bviral\b|\bgoes viral|\bwent viral|\bblows? up|\breact\w*|\bupset\w*|"
    r"\bfuming|\bsparks?\b|\bslams?\b|\bembarrass\w*|\bcringe|\bawkward|\bheartwarming|\bwholesome|\btear\w*|"
    r"\bsweet gesture|\bkindness|\bpraised?\b|\bhero\b|\bbanned?\b|\bfired\b|\bquits?\b|\bkicked out|\brefused?\b|"
    r"\bcalled (?:the )?(?:cops|police)|\bsecretly|\bcaught\b|\bconfront\w*|\bexplains why\b", re.I)
_BAD_GATES = ("not about the US", "not about food & drink", "a single local business, not a national brand",
              "no US brand or US place named")


def _texts(s):
    return [m.get("title") or "" for m in s.get("members", [])]


def candidate(s, now=None):
    """(ok, brand) — an everyday brand story people talk about, which the index does not count."""
    from engine.everyday import index_gate, english_only, FOOD_BRANDS, GROCERS
    from engine import narrative_index as ni
    from engine.tipping import PROMO_RX, tip_gate
    now = now or time.time()
    t = s.get("title") or ""
    if not t or not english_only(t) or SKIP_RX.search(t):
        return False, None
    low = t.lower().replace("’", "'")
    found = [b for b in list(FOOD_BRANDS) + list(GROCERS) if ni._has_any(low, [b])]   # food brand or grocer (not TikTok)
    if not found:
        return False, None
    brand = max(found, key=len)
    if not PERSON_RX.search(t) or not REACT_RX.search(t):
        return False, None                       # a person AND a reaction: that is what people comment on
    if PROMO_RX.search(t) and not REACT_RX.search(t):
        return False, None                       # a launch with nobody reacting is an ad
    ok, why = index_gate(t, _texts(s), s.get("company"))
    if ok and tip_gate(t, [], brand)[0]:
        return False, None                       # real trouble goes to "Will it tip?"
    if not ok and (why in _BAD_GATES or (why or "").startswith("off-topic")):
        return False, None                       # not US / not food / one local place: out of scope
    if int(s.get("outlets") or 0) < VIRAL_MIN_OUTLETS:
        return False, None
    newest = max([m.get("ts") or 0 for m in s.get("members", [])] or [s.get("ts") or 0])
    if newest < now - VIRAL_FRESH_H * 3600:
        return False, None
    return True, brand


def is_viral(n_or_snap):
    snap = n_or_snap if isinstance(n_or_snap, dict) else json.loads(n_or_snap.snapshot or "{}")
    return bool((snap.get("bet") or {}).get("viral"))


def listed_today(session, now, arena="everyday"):
    from db.models import Narrative
    rows = (session.query(Narrative.snapshot).filter(Narrative.arena == arena, Narrative.listed_at >= now - 86400,
                                                     Narrative.status.notin_(["DELISTED", "SPOTTED", "CONFIRMED", "EXPIRED"]))
            .all())
    return sum(1 for (sn,) in rows if is_viral(json.loads(sn or "{}")))


VIRAL_RULE_V = 2


def recheck(session, now):
    """Viral listings from an older, looser rule that the current rule would not make are delisted
    (bets refunded) — once per story."""
    from db.models import Narrative
    from engine.story_listing import _settle
    n_done = 0
    for n in session.query(Narrative).filter(Narrative.status == "LISTED").all():
        snap = json.loads(n.snapshot or "{}")
        b = snap.get("bet") or {}
        if not b.get("viral") or b.get("rule_v", 1) >= VIRAL_RULE_V or b.get("escalated_to"):
            continue
        t = snap.get("listed_title") or n.title
        ok = PERSON_RX.search(t or "") and REACT_RX.search(t or "") and not SKIP_RX.search(t or "")
        if ok:
            b["rule_v"] = VIRAL_RULE_V
            n.snapshot = json.dumps(snap)
            continue
        n.status, n.closed_at = "DELISTED", now
        snap["delisted"] = {"at": now, "why": "not a viral people story (stricter rule, 28 Nov 2026)"}
        n.snapshot = json.dumps(snap)
        _settle(session, n.sid, refund=True)
        n_done += 1
        print(f"🧹 Viral story delisted (stricter rule)  {n.sid}  {t[:70]}")
    return n_done


def list_new(session, stories, now, arena="everyday"):
    """List up to VIRAL_DAILY_CAP viral stories a day; returns how many were listed now."""
    from db.models import Narrative, NarrativePoint
    try:
        recheck(session, now)
    except Exception as e:
        print(f"⚠️ viral recheck: {e}")
    from engine.story_listing import _snapshot, _inputs_from_live, _same_brand, _price_point
    from engine.tipping import rule as tip_rule
    room = VIRAL_DAILY_CAP - listed_today(session, now, arena)
    if room <= 0:
        return 0
    busy = [(json.loads(n.snapshot or "{}").get("bet") or {}).get("company") or n.title
            for n in session.query(Narrative).filter(Narrative.status == "LISTED", Narrative.arena == arena).all()]
    picks = []
    for s in stories:
        ok, brand = candidate(s, now)
        if not ok or any(_same_brand(brand, b) for b in busy if b):
            continue
        if session.query(Narrative).filter(Narrative.sid == s["sid"]).first():
            continue
        picks.append((int(s.get("outlets") or 0), s.get("story_score") or 0, s, brand))
    picks.sort(key=lambda x: (-x[0], -x[1]))
    used = 0
    rl = tip_rule(session)
    for o, _, s, brand in picks:
        if used >= room:
            break
        if any(_same_brand(brand, b) for b in busy if b):
            continue
        closes = now + VIRAL_DAYS * 86400
        from engine.social_first import shown
        bet = {"kind": "tip", "viral": True, "rule_v": VIRAL_RULE_V, "company": shown(brand), "start": o,
               "target": max(VIRAL_MIN, o + VIRAL_GROWTH), "talk": False, "tipped": False, "closes_at": closes,
               "rule": {"outlets": VIRAL_MIN, "growth": VIRAL_GROWTH, "days": VIRAL_DAYS, "rate": rl.get("rate"),
                        "provisional": rl.get("provisional", True)}}
        snap = {**_snapshot(s), "score_inputs": {**_inputs_from_live(s), "at": now}, "listed_title": s["title"],
                "bet": bet, "origin": "viral"}
        n = Narrative(sid=s["sid"], title=s["title"], url=s["url"], source=s["source"], arena=arena, status="LISTED",
                      first_seen=s.get("first_seen"), listed_at=now, closes_at=closes, list_score=s["story_score"],
                      last_score=s["story_score"], peak_score=s["story_score"], last_seen=now, snapshot=json.dumps(snap))
        session.add(n)
        session.add(NarrativePoint(sid=s["sid"], ts=now, score=s["story_score"]))
        try:
            _price_point(session, n, now)
        except Exception:
            pass
        busy.append(brand)
        used += 1
        try:                                     # the reality check follows viral stories too ("attention before trouble")
            from engine.reality import log_listed
            log_listed(session, s["sid"], s["title"], brand)
            from db.models import ForwardLog
            session.add(ForwardLog(ts=now, kind="viral", brand=None, sid=s["sid"], title=s["title"][:300], label="viral listed"))
        except Exception as e:
            print(f"⚠️ viral log: {e}")
        print(f"📌 Story listed  {s['sid']}  VIRAL  [{o} outlets]  {s['title'][:80]}")
    return used


# ─────────────────────────────────────────────
# ESCALATION: a viral story turns into trouble (28 Nov 2026)
# ─────────────────────────────────────────────
ESCALATE_WINDOW_D = 7      # trouble within a week of the viral listing counts as an escalation


def _trouble(title, brand):
    from engine.everyday import index_gate
    from engine.tipping import tip_gate
    return bool(title) and index_gate(title, [], brand)[0] and tip_gate(title, [], brand)[0]


def _link(session, v, vsnap, tip_sid, tip_title, now, how):
    """Mark the pair and record the escalation for the track record."""
    from db.models import ForwardLog, Narrative
    vsnap["escalated_to"] = tip_sid
    (vsnap.setdefault("bet", {}))["escalated_to"] = tip_sid
    v.snapshot = json.dumps(vsnap)
    t = session.query(Narrative).filter(Narrative.sid == tip_sid).first()
    if t:
        tsnap = json.loads(t.snapshot or "{}")
        tsnap["escalated_from"] = v.sid
        (tsnap.setdefault("bet", {}))["escalated_from"] = v.sid
        t.snapshot = json.dumps(tsnap)
    hours = max(0.0, (now - (v.listed_at or now)) / 3600)
    try:
        from engine.reality import brand_key
        session.add(ForwardLog(ts=now, kind="escalated", brand=brand_key((vsnap.get("bet") or {}).get("company")),
                               sid=tip_sid, title=(tip_title or "")[:300], label=f"{hours:.0f} h after viral",
                               weight=round(hours, 1)))
    except Exception as e:
        print(f"⚠️ escalation log: {e}")
    print(f"⚠️ Viral story ESCALATED  {v.sid} → {tip_sid}  ({how}, {hours:.0f} h)  {(tip_title or '')[:70]}")


def escalate(session, stories, now, arena="everyday"):
    """For every viral story of the last week: has it turned into trouble?
    a) a separate trouble story about the same brand & topic was listed → link the two;
    b) the viral story's own coverage now has trouble headlines → open a linked "Will it tip?" story that
       follows the same news group (its id is the viral id + "x")."""
    from db.models import Narrative, NarrativePoint
    from engine.story_listing import _snapshot, _inputs_from_live, _same_brand, _same_topic, _price_point
    from engine.tipping import rule as tip_rule, make_bet
    session.flush()                              # stories listed earlier in this same run must be visible
    live = {s["sid"]: s for s in stories}
    done = 0
    vir = [n for n in session.query(Narrative).filter(Narrative.arena == arena,
                                                       Narrative.listed_at >= now - ESCALATE_WINDOW_D * 86400).all()
           if is_viral(n)]
    if not vir:
        return 0
    tips = [n for n in session.query(Narrative).filter(Narrative.arena == arena, Narrative.status == "LISTED").all()
            if not is_viral(n) and (json.loads(n.snapshot or "{}").get("bet") or {}).get("kind") == "tip"]
    for v in vir:
        vsnap = json.loads(v.snapshot or "{}")
        if vsnap.get("escalated_to") or (vsnap.get("bet") or {}).get("escalated_to"):
            continue
        brand = (vsnap.get("bet") or {}).get("company")
        # a) a trouble story about it is already on the board
        hit = next((t for t in tips if (t.listed_at or 0) >= (v.listed_at or 0)
                    and _same_brand((json.loads(t.snapshot or "{}").get("bet") or {}).get("company") or t.title, brand)
                    and (_same_topic(t.title, vsnap.get("listed_title") or v.title, brand)
                         or VIRALISH_RX.search(t.title or ""))), None)
        if hit:
            _link(session, v, vsnap, hit.sid, hit.title, now, "trouble story listed")
            done += 1
            continue
        # b) the viral story's own news group now carries trouble headlines
        s = live.get(v.sid)
        if not s:
            continue
        bad = [m for m in s.get("members", []) if (m.get("ts") or 0) >= (v.listed_at or 0) and _trouble(m.get("title"), brand)]
        if not bad:
            continue
        sid = (v.sid + "x")[:16]
        if session.query(Narrative).filter(Narrative.sid == sid).first():
            continue
        rl = tip_rule(session)
        closes = now + rl["days"] * 86400
        title = bad[-1]["title"]
        bet = make_bet({**s, "company": brand}, closes, rl)
        bet["escalated_from"] = v.sid
        snap = {**_snapshot(s), "score_inputs": {**_inputs_from_live(s), "at": now}, "listed_title": title,
                "bet": bet, "origin": "escalated", "follows": v.sid, "escalated_from": v.sid}
        t = Narrative(sid=sid, title=title, url=bad[-1].get("url") or s["url"], source=bad[-1].get("source") or s["source"],
                      arena=arena, status="LISTED", first_seen=s.get("first_seen"), listed_at=now, closes_at=closes,
                      list_score=s["story_score"], last_score=s["story_score"], peak_score=s["story_score"], last_seen=now,
                      snapshot=json.dumps(snap))
        session.add(t)
        session.add(NarrativePoint(sid=sid, ts=now, score=s["story_score"]))
        session.flush()
        try:
            _price_point(session, t, now)
            from engine.reality import log_listed
            log_listed(session, sid, title, brand)
        except Exception:
            pass
        _link(session, v, vsnap, sid, title, now, "its own coverage turned into trouble")
        done += 1
    return done


VIRALISH_RX = re.compile(r"\bviral\b|\bvideo\b|\btiktok\w*|\bclip\b|\bpost\b|\bcustomer'?s?\b", re.I)
