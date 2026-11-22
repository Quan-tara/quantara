"""
Phase 2, bridges — does a story jump between separate groups of people? (22 Nov 2026, recorded only)
======================================================================================================
Network science (Granovetter's weak ties, Centola's complex contagion): a story that stays inside one
circle of accounts that follow each other dies there; one that shows up in several separate circles has
crossed a bridge and can reach the mainstream.

For every listed 'Will it tip?' story, every BRIDGES_EVERY_H hours (read-only Bluesky API):
  1. the posts about it (same search as the voices check), distinct authors
  2. for up to SAMPLE authors (most engaged first): who they follow (first page) and their follower counts
  3. groups = separate clusters among those authors when follow links join them (connected components)
Recorded: authors, groups, share of the biggest group, combined audience, big accounts (10k+ followers).
An approximation (one page of follows per author), good enough to see a story spread from one circle to many.
"""

import time

import requests

from db.models import Narrative, StoryBridges

BRIDGES_EVERY_H = 6
SAMPLE = 25
PER_RUN = 3
BIG = 10000


def _get(path, params):
    from engine import social as S
    r = requests.get(f"{S.PDS}/{path}", params=params, timeout=20, headers={"Authorization": f"Bearer {S._login()}"})
    if r.status_code != 200:
        raise RuntimeError(f"{path} HTTP {r.status_code}")
    return r.json()


def groups(authors, follows):
    """Connected components among `authors` (list of dids) given follows: did → set of followed dids."""
    parent = {a: a for a in authors}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a in authors:
        for b in follows.get(a, ()):
            if b in parent:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
    sizes = {}
    for a in authors:
        r = find(a)
        sizes[r] = sizes.get(r, 0) + 1
    return sorted(sizes.values(), reverse=True)


def measure(posts):
    eng = {}
    for p in posts:
        a = p.get("author") or {}
        did = a.get("did")
        if not did:
            continue
        e = 1 + (p.get("likeCount") or 0) + 2 * ((p.get("repostCount") or 0) + (p.get("quoteCount") or 0))
        eng[did] = eng.get(did, 0) + e
    sample = [d for d, _ in sorted(eng.items(), key=lambda x: -x[1])[:SAMPLE]]
    follows, audience, big = {}, 0, 0
    if sample:
        prof = _get("app.bsky.actor.getProfiles", [("actors", d) for d in sample])
        for p in prof.get("profiles", []):
            f = p.get("followersCount") or 0
            audience += f
            big += f >= BIG
    for d in sample:
        try:
            fl = _get("app.bsky.graph.getFollows", {"actor": d, "limit": 100})
            follows[d] = {x.get("did") for x in fl.get("follows", [])}
        except Exception:
            follows[d] = set()
    g = groups(sample, follows)
    return {"posts": len(posts), "authors": len(eng), "sampled": len(sample), "clusters": len(g),
            "largest_share": round(g[0] / len(sample), 2) if g else None, "audience": audience, "big_accounts": big}


def run(session):
    from engine import social as S
    from engine.voices import _search_posts
    import json
    if not S.configured() or time.time() < S._state.get("blocked_until", 0):
        return 0
    done = 0
    for n in session.query(Narrative).filter(Narrative.status == "LISTED").all():
        if done >= PER_RUN:
            break
        snap = json.loads(n.snapshot or "{}")
        b = snap.get("bet") or {}
        if b.get("kind") != "tip":
            continue
        last = session.query(StoryBridges.ts).filter(StoryBridges.sid == n.sid).order_by(StoryBridges.ts.desc()).first()
        if last and time.time() - last[0] < BRIDGES_EVERY_H * 3600:
            continue
        brand = b.get("company") or snap.get("lead_brand")
        terms = S.story_terms([snap.get("listed_title") or n.title], brand)
        rec = StoryBridges(sid=n.sid, ts=time.time())
        if not terms:
            rec.status = "NO_TERMS"
        else:
            try:
                posts = [p for p in _search_posts(f'"{terms[0]}"')
                         if S._matches((p.get("record") or {}).get("text"), terms[1])]
                m = measure(posts)
                for k, v in m.items():
                    setattr(rec, k, v)
            except Exception as e:
                rec.status = "ERROR"
                print(f"⚠️ bridges {n.sid}: {e}")
        session.add(rec)
        done += 1
    session.commit()
    if done:
        print(f"🌉 bridges: {done} listed stories checked on Bluesky")
    return done
