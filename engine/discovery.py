"""
Story discovery — find food stories on Bluesky BEFORE the news has them
=======================================================================
Every collector run (15 min) we check a rotating batch of food brands on Bluesky:
how many English posts mention the brand in the last 3 hours?
Each brand is compared with its OWN normal level (median of its readings over the last
7 days). A SPIKE = at least 15 posts and at least 3× normal.

For a spiking brand we work out what people are talking about (the most common words in
the posts, e.g. "Wendy's → menu, price, discontinued"). Then:
  • it shows up in the moderators' candidates panel as "Rising on Bluesky"
  • the food & drink arena searches Google News for "<brand> <those words>" — so news
    articles about it are pulled in early, and the story can be listed as soon as the
    normal listing rules are met (social posts alone never list a story)

Full cycle over all brands ≈ 1.5 hours. Brands are warmed up for ~6 readings before a
spike can be called (a brand needs a normal level first).
"""

import json
import re
import statistics
import time
from collections import Counter

from db.database import SessionLocal
from db.models import BrandBuzz

WINDOW_H       = 3
BATCH          = 20          # brands per collector run
HISTORY_D      = 7
MIN_HISTORY    = 6
SPIKE_X        = 3.0
SPIKE_MIN      = 15
RISING_KEEP_H  = 6           # a spike stays "rising" this long
_state = {"pos": 0, "warned": False}

# brands whose name is also an everyday word or place are left out (subway, target, iceland …)
_AMBIGUOUS = {"subway", "target", "iceland", "co-op", "costa", "pret", "coke", "m&s", "h-e-b", "mars",
              "leon", "sonic", "corona", "innocent", "prime hydration", "just eat", "monster energy",
              "mcdonalds", "dominos", "nestle", "whole foods", "trader joe's", "walkers", "heinz"}
_EXTRA = ["heinz ketchup", "monster energy drink", "prime drink"]


def brands():
    from engine.everyday import FOOD_BRANDS, GROCERS
    out = []
    for b in FOOD_BRANDS + GROCERS + _EXTRA:
        if b not in _AMBIGUOUS and b not in out:
            out.append(b)
    return out


_STOP = set("""
the a an and or but if then so to of in on at for with by from as is are was were be been being it its
this that these those i you he she we they me my your our their his her them us just not no yes do does
did done have has had will would can could should what when where who why how all any some more most
very really too also than only about out up down over get got like one two new now today time year
lol im ive dont cant wont thats theres youre its it's i'm i've don't can't won't that's there's you're
via amp https http www com co uk org people going still even much make made good bad back want need
know think see say said look looks thing things way day days week ever never always every
""".split())


def _topic(texts, brand):
    """Most common words in the posts besides the brand → what people are talking about."""
    from engine.everyday import BUZZ_GENERIC, _norm_word
    bw = set(re.findall(r"[a-z0-9]+", brand.lower())) | {_norm_word(brand.lower()).replace(" ", "")}
    c = Counter()
    for t in texts:
        seen = set()
        for w in re.findall(r"[a-z][a-z'’-]{3,}", (t or "").lower()):
            w = _norm_word(w)
            if not w or w in _STOP or w in bw or w in BUZZ_GENERIC or w in seen or w.startswith("http"):
                continue
            seen.add(w)
            c[w] += 1
    need = max(3, len(texts) // 10)              # said by at least ~10% of the posts
    return [w for w, n in c.most_common(8) if n >= need][:4]


def run_discovery():
    from engine.social import configured, search, _state as sstate
    if not configured() or time.time() < sstate["blocked_until"]:
        return
    all_b = brands()
    batch = [all_b[(_state["pos"] + i) % len(all_b)] for i in range(min(BATCH, len(all_b)))]
    _state["pos"] = (_state["pos"] + len(batch)) % len(all_b)
    session = SessionLocal()
    spikes, checked = [], 0
    try:
        now = time.time()
        for b in batch:
            rec = BrandBuzz(brand=b, ts=now)
            try:
                res = search(f'"{b}"', hours=WINDOW_H)
                prev = [r.posts for r in session.query(BrandBuzz)
                        .filter(BrandBuzz.brand == b, BrandBuzz.status == "OK",
                                BrandBuzz.ts >= now - HISTORY_D * 86400).all() if r.posts is not None]
                base = statistics.median(prev) if len(prev) >= MIN_HISTORY else None
                rec.posts, rec.baseline = res["brand_total"], base
                rec.spike = bool(base is not None and res["brand_total"] >= SPIKE_MIN
                                 and res["brand_total"] >= SPIKE_X * max(base, 3))
                if rec.spike or res["brand_total"] >= SPIKE_MIN:
                    rec.words = json.dumps(_topic(res["texts"], b))
                    rec.top_post = json.dumps(res["top_post"]) if res["top_post"] else None
                if rec.spike:
                    spikes.append(f"{b} {res['brand_total']} posts ({res['brand_total'] / max(base, 3):.1f}×) "
                                  f"→ {', '.join(json.loads(rec.words or '[]'))}")
            except Exception as e:
                rec.status, rec.error = "ERROR", str(e)[:300]
                if "429" in str(e) or "login" in str(e):
                    session.add(rec)
                    break
            session.add(rec)
            checked += 1
            time.sleep(0.5)
        session.commit()
        print(f"🔎 discovery: {checked} brands checked on Bluesky"
              + (f" — RISING: {'; '.join(spikes)}" if spikes else " — nothing rising"))
    finally:
        session.close()


def rising(session, hours=RISING_KEEP_H):
    """Brands spiking in the last few hours, newest first, one entry per brand."""
    rows = (session.query(BrandBuzz).filter(BrandBuzz.spike.is_(True), BrandBuzz.ts >= time.time() - hours * 3600)
            .order_by(BrandBuzz.ts.desc()).all())
    out, seen = [], set()
    for r in rows:
        if r.brand in seen:
            continue
        seen.add(r.brand)
        out.append({"brand": r.brand, "ts": r.ts, "posts": r.posts, "baseline": r.baseline,
                    "x": round(r.posts / max(r.baseline or 0, 3), 1),
                    "words": json.loads(r.words or "[]"),
                    "top_post": json.loads(r.top_post) if r.top_post else None})
    return out


def overview(session):
    """For /api/discovery: what is rising, and how far the warm-up is."""
    now = time.time()
    rows = session.query(BrandBuzz).filter(BrandBuzz.ts >= now - HISTORY_D * 86400, BrandBuzz.status == "OK").all()
    per = Counter(r.brand for r in rows)
    latest = {}
    for r in rows:
        if r.brand not in latest or r.ts > latest[r.brand].ts:
            latest[r.brand] = r
    busiest = sorted(latest.values(), key=lambda r: r.posts or 0, reverse=True)[:15]
    return {"brands": len(brands()), "warmed_up": sum(1 for b in brands() if per.get(b, 0) >= MIN_HISTORY),
            "rising": rising(session),
            "busiest_now": [{"brand": r.brand, "posts_3h": r.posts, "normal": r.baseline,
                             "words": json.loads(r.words or "[]")} for r in busiest]}
