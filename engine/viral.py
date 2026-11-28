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
    r"\brecall\w*\b|\boutbreak\b|\bsalmonella\b|\blisteria\b", re.I)
REACT_RX = re.compile(
    r"\bbacklash|\boutrage|\bfurious|\bangry|\bslam\w*|\bdisgust\w*|\bgross\b|\bweird\b|\bwild\b|\bshock\w*|\bstun\w*|"
    r"\bdebate|\bdivide\w*|\bsplit\b|\bcontrovers\w*|\bcalls? out|\bexpos\w*|\breveal\w*|\bconfess\w*|\bcomplain\w*|"
    r"\bcan'?t believe|\bhilarious|\broast\w*|\bmock\w*|\btroll\w*|\bpetty|\bscam\w*|\bripoff|\bdrama\b", re.I)
_BAD_GATES = ("not about the US", "not about food & drink", "a single local business, not a national brand",
              "no US brand or US place named")


def _texts(s):
    return [m.get("title") or "" for m in s.get("members", [])]


def candidate(s, now=None):
    """(ok, brand) — an everyday brand story people talk about, which the index does not count."""
    from engine.everyday import index_gate, english_only, FOOD_BRANDS
    from engine import narrative_index as ni
    from engine.tipping import PROMO_RX, tip_gate
    now = now or time.time()
    t = s.get("title") or ""
    if not t or not english_only(t) or SKIP_RX.search(t):
        return False, None
    low = t.lower().replace("’", "'")
    found = [b for b in FOOD_BRANDS if ni._has_any(low, [b])]          # a FOOD brand (not TikTok, not Amazon)
    if not found:
        return False, None
    brand = max(found, key=len)
    if not PEOPLE_RX.search(t):
        return False, None
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


def list_new(session, stories, now, arena="everyday"):
    """List up to VIRAL_DAILY_CAP viral stories a day; returns how many were listed now."""
    from db.models import Narrative, NarrativePoint
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
        bet = {"kind": "tip", "viral": True, "company": shown(brand), "start": o,
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
        print(f"📌 Story listed  {s['sid']}  VIRAL  [{o} outlets]  {s['title'][:80]}")
    return used
