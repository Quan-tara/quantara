"""
Big narratives ("constellations") — test phase, recorded only
=============================================================
Shiller: what moves people is rarely one story but a larger narrative that many stories belong to,
across brands, mutating and coming back ("corporations are ripping us off" → nachos got smaller,
Doritos bag half air, Kroger prices…). So every story that counts in the Food Reputation Index is
tagged with the big narrative(s) it belongs to, and each narrative gets a strength over time.

  tagging    rules first (cheap, testable); a headline no rule recognises is asked to Gemini once,
             at most AI_PER_RUN a reading, and the answer is cached (theme_tags) — so the spot check
             never runs short of AI quota
  strength   every hour: sum of the weights of the counting stories in the narrative, the brands in it
  status     warming up (under 2 days of history) · quiet · steady · rising (≥ 1.5× its normal level of
             the last 7 days, strength ≥ 10, 2+ brands) · back (quiet for a week, now strong again)
             · new brand: a brand that wasn't in this narrative in the last 14 days
  forward    when a narrative turns rising or back, each tracked brand in it is logged as a
             'narrative' warning; the track record checks them like every other warning

Nothing here changes the index, the gates, listing or bets. It is judged by the track record first.
"""

import json
import os
import re
import statistics
import time

import requests

from db.models import ThemeTag, ThemeTick

DAY = 86400
TICK_EVERY_S = 55 * 60
AI_PER_RUN = 3
RISE_X, RISE_MIN, RISE_BRANDS = 1.5, 10.0, 2
QUIET, QUIET_DAYS = 3.0, 7
HISTORY_MIN_TICKS = 48              # ≈ 2 days of hourly readings before "rising" can be called

THEMES = {
    "greedflation": ("Greedflation & shrinkflation",
        r"\bprice hikes?\b|\bprice increases?\b|\braises? prices\b|\braising prices\b|\bprices?\b.{0,30}\b(?:up|higher|soar|jump|surge)|"
        r"\bshrinkflation|\bsmaller\b|\bshr[iu]nk|\bportions?\b|\bdownsiz|\bless food\b|\bsame price\b|\bexpensive\b|\boverpric|"
        r"\brip-?off\b|\bgreed|\bprice gouging|\bcosts? more\b|\$\d+(?:\.\d+)? (?:burger|meal|sandwich|coffee|footlong|combo)"),
    "workers": ("Workers & unions",
        r"\bunion|\bbaristas?\b|\bstrikes?\b|\bwalk ?outs?\b|\bwages?\b|\blayoffs?\b|\blaid off\b|\blaying off\b|\bunpaid\b|"
        r"\bunderpaid\b|\bstaffing\b|\bunderstaff|\bworkers?\b|\bemployees?\b|\bfired\b|\bfranchisees?\b|\bpicket"),
    "safety": ("Food safety scares",
        r"\brecall|\boutbreak|\bsalmonella|\blisteria|\be\.? ?coli\b|\bsick(?:ened)?\b|\bhospitali|\bcontaminat|\bfood poisoning|"
        r"\bforeign object|\bglove\b|\bchoked\b|\bfound (?:a |an )?\w+ in\b|\bundercooked|\braw chicken|\bmold|\bpests?\b|\brodent|\bcockroach"),
    "ingredients": ("Ingredients & health",
        r"\bdyes?\b|\bred (?:no\.? ?)?40\b|\bseed oils?\b|\bartificial\b|\bultra-?processed|\badditives?\b|\bpreservatives?\b|"
        r"\bhigh[- ]fructose|\bingredients?\b|\bmaha\b|\brfk\b|\bmicroplastic|\bpfas\b|\bheavy metals?\b|\blead\b.{0,20}\b(?:levels?|found)|\bcarcinogen|\bcancer\b"),
    "fees": ("Tipping & fees",
        r"\btip(?:s|ping)?\b|\bservice fees?\b|\bdelivery fees?\b|\bjunk fees?\b|\bhidden fees?\b|\bsurcharges?\b|\bmembership fees?\b|\bfee hikes?\b|\bnew fee\b"),
    "culture": ("Culture-war boycotts",
        r"\bwoke\b|\banti-woke\b|\bdei\b|\bdiversity\b|\bpride\b|\btrans(?:gender)?\b|\blgbt|\bconservatives?\b|\bmaga\b|\btrump\b|"
        r"\bisrael|\bgaza\b|\bpalestin|\bpolitic|\bculture war\b|\bleft-wing\b|\bright-wing\b"),
    "automation": ("Automation & AI",
        r"\bai\b|\bartificial intelligence\b|\bkiosks?\b|\brobots?\b|\bautomat|\bself-checkout|\bapp-only\b|\bcashierless\b|\bdrive-thru ai\b|\bvoice ai\b"),
}
_RX = {k: re.compile(rx, re.I) for k, (_, rx) in THEMES.items()}
LABELS = {k: v[0] for k, v in THEMES.items()}


def tag_rules(title):
    t = (title or "").replace("’", "'")
    return [k for k, rx in _RX.items() if rx.search(t)]


_PROMPT = """You sort US food & drink news headlines into big public narratives. Headline: "{title}"
Narratives: greedflation (prices, shrinkflation, portions, greed), workers (pay, unions, layoffs, staff),
safety (illness, recalls, contamination, foreign objects), ingredients (dyes, additives, health worries),
fees (tipping, service or delivery fees), culture (political or culture-war boycotts),
automation (AI, kiosks, robots replacing staff).
Answer JSON only: {{"themes": [zero, one or two of the keys above]}}"""


_ask_state = {"ok": 0, "last_error": None}


def ask_json(prompt, max_tokens=80):
    """One Gemini call → parsed JSON dict, or None (no key, no model found yet, quota, error).
    Uses the model the spot check / short-title job already found, so no extra discovery calls."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    try:
        from engine.spot_check import _model, GEMINI
    except Exception:
        return None
    models = [os.getenv("GEMINI_MODEL"), _model.get("name")]
    try:
        from engine.titles import _state
        models.append(_state.get("model"))
    except Exception:
        pass
    models = [m for m in dict.fromkeys(models) if m]
    if not models:                       # nobody has looked the model up since the restart: do it once here
        if "list" not in _model:
            try:
                from engine.titles import _gemini_models
                _model["list"] = _gemini_models(key)[:3]
            except Exception:
                _model["list"] = []
        models = list(_model.get("list") or [])
    for m in models:
        try:
            r = requests.post(f"{GEMINI}/models/{m}:generateContent", params={"key": key}, timeout=8,
                              json={"contents": [{"parts": [{"text": prompt}]}],
                                    "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens,
                                                         "responseMimeType": "application/json"}})
        except Exception:
            return None
        if r.status_code != 200:
            _ask_state["last_error"] = f"{m}: HTTP {r.status_code}"
            continue
        if not _model.get("name"):
            print(f"🤖 AI helper working (model {m}): tone, narrative tags, brand learning")
        _model["name"] = _model.get("name") or m
        try:
            parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}]
            txt = "".join(p.get("text", "") for p in parts)
            mm = re.search(r"\{.*\}", txt, re.S)
            _ask_state["ok"] += 1
            return json.loads(mm.group(0)) if mm else None
        except Exception:
            return None
    return None


def _ai_tag(title):
    got = ask_json(_PROMPT.format(title=title.replace('"', "'")[:300]), 60)
    if got is None:
        return None
    return [k for k in (got.get("themes") or []) if k in THEMES][:2]


def tags_for(session, titles):
    """{title: [themes]} — cached; rules first, then at most AI_PER_RUN new headlines asked to the AI."""
    titles = [t for t in dict.fromkeys(titles) if t]
    cached = {r.title: json.loads(r.themes or "[]") for r in
              session.query(ThemeTag).filter(ThemeTag.title.in_([t[:300] for t in titles])).all()} if titles else {}
    out, asked = {}, 0
    for t in titles:
        k = t[:300]
        if k in cached:
            out[t] = cached[k]
            continue
        th, src = tag_rules(t), "rules"
        if not th and asked < AI_PER_RUN:
            asked += 1
            ai = _ai_tag(t)
            if ai is None:
                out[t] = []                          # AI not available now: try again next reading
                continue
            th, src = ai, "ai"
        session.add(ThemeTag(title=k, themes=json.dumps(th), source=src, ts=time.time()))
        out[t] = th
    return out


def record(session, rows, now=None):
    """Called each index reading with all story rows. Writes one tick per narrative per hour and logs
    rising / back narratives to the forward log. Recorded only."""
    now = now or time.time()
    last = session.query(ThemeTick.ts).order_by(ThemeTick.ts.desc()).first()
    if last and now - last[0] < TICK_EVERY_S:
        return None
    counting = [r for r in rows if (r.get("weighted") or 0) > 0]
    tags = tags_for(session, [r.get("title") for r in counting])
    from engine.everyday import brand_in
    agg = {k: {"strength": 0.0, "stories": 0, "brands": [], "top": []} for k in THEMES}
    for r in counting:
        b = (r.get("company") or brand_in([r.get("title") or ""]) or "").strip()
        for k in tags.get(r.get("title"), []):
            a = agg[k]
            a["strength"] += r["weighted"]
            a["stories"] += 1
            if b and b.lower() not in [x.lower() for x in a["brands"]]:
                a["brands"].append(b)
            a["top"].append([r.get("title"), r.get("sid"), r["weighted"], b])
    states = {}
    for k, a in agg.items():
        a["top"].sort(key=lambda x: -x[2])
        before = status(session, k, now)             # status from history, before this tick
        session.add(ThemeTick(ts=now, theme=k, strength=round(a["strength"], 1), stories=a["stories"],
                              brands=json.dumps(a["brands"]), top=json.dumps(a["top"][:5])))
        after = _judge(session, k, a, now)
        states[k] = after
        if after["status"] in ("rising", "back") and before["status"] not in ("rising", "back"):
            _log(session, k, a, after["status"])
    session.commit()
    try:
        record_emerging(session, rows, tags)
    except Exception as e:
        session.rollback()
        print(f"⚠️ emerging narratives: {e}")
    return states


def _history(session, k, now, days):
    try:
        from engine.attention import method_start
        since = max(now - days * DAY, method_start(session))
    except Exception:
        since = now - days * DAY
    return (session.query(ThemeTick).filter(ThemeTick.theme == k, ThemeTick.ts >= since,
                                            ThemeTick.ts < now - 1).order_by(ThemeTick.ts).all())


def _judge(session, k, a, now):
    hist = _history(session, k, now, 7)
    vals = [h.strength for h in hist]
    seen = set()
    for h in _history(session, k, now, 14):
        seen |= {x.lower() for x in json.loads(h.brands or "[]")}
    new_brands = [b for b in a["brands"] if b.lower() not in seen] if hist else []
    s = a["strength"]
    if len(vals) < HISTORY_MIN_TICKS:
        st = "warming up"
    else:
        normal = statistics.median(vals)
        quiet_week = all(v < QUIET for v in vals[-QUIET_DAYS * 24:]) if len(vals) >= QUIET_DAYS * 24 else False
        if quiet_week and s >= RISE_MIN:
            st = "back"
        elif s >= RISE_MIN and len(a["brands"]) >= RISE_BRANDS and s >= RISE_X * max(normal, 1.0):
            st = "rising"
        elif s < QUIET:
            st = "quiet"
        else:
            st = "steady"
    return {"status": st, "new_brands": new_brands, "normal": round(statistics.median(vals), 1) if vals else None}


def status(session, k, now=None):
    """Status from the latest stored tick (used before a new tick is written)."""
    now = now or time.time()
    t = session.query(ThemeTick).filter(ThemeTick.theme == k).order_by(ThemeTick.ts.desc()).first()
    try:
        from engine.attention import method_start
        if t and t.ts < method_start(session):
            t = None
    except Exception:
        pass
    if not t:
        return {"status": "warming up", "new_brands": [], "normal": None}
    a = {"strength": t.strength, "brands": json.loads(t.brands or "[]")}
    return _judge(session, k, a, t.ts)


def _log(session, k, a, st):
    try:
        from db.models import ForwardLog
        from engine.reality import brand_key, DEDUPE_D, _dup_key
        from engine.everyday import brand_in
        now = time.time()
        known = {_dup_key(b, t) for (b, t) in session.query(ForwardLog.brand, ForwardLog.title)
                 .filter(ForwardLog.kind == "narrative", ForwardLog.ts >= now - DEDUPE_D * DAY).all()}
        for title, sid, w, comp in a["top"]:
            b = brand_key(comp) or brand_key(brand_in([title or ""]))
            if not b or _dup_key(b, title) in known:      # one warning per brand per week
                continue
            known.add(_dup_key(b, title))
            session.add(ForwardLog(ts=now, kind="narrative", brand=b, sid=sid, title=(title or "")[:300],
                                   label=f"{LABELS[k]} · {st}", weight=w))
        print(f"🧶 Narrative {LABELS[k]} is {st}: {', '.join(a['brands'][:6])}")
    except Exception as e:
        print(f"⚠️ narrative log error: {e}")


def board(session, now=None):
    """For /api/narratives: each narrative now, strongest first."""
    now = now or time.time()
    out = []
    for k in THEMES:
        t = session.query(ThemeTick).filter(ThemeTick.theme == k).order_by(ThemeTick.ts.desc()).first()
        if not t:
            out.append({"key": k, "label": LABELS[k], "strength": 0, "stories": 0, "brands": [], "top": [],
                        "status": "warming up", "new_brands": [], "normal": None, "series": []})
            continue
        st = _judge(session, k, {"strength": t.strength, "brands": json.loads(t.brands or "[]")}, t.ts)
        series = [[h.ts, h.strength] for h in _history(session, k, now + 1, 7)]
        out.append({"key": k, "label": LABELS[k], "strength": t.strength, "stories": t.stories,
                    "brands": json.loads(t.brands or "[]"), "top": json.loads(t.top or "[]"), "ts": t.ts, **st,
                    "series": series[-168:], "curve": phase(series)})
    out.sort(key=lambda x: -x["strength"])
    try:
        from engine.attention import method_start
        ticks = session.query(ThemeTick.ts).filter(ThemeTick.ts >= method_start(session)).distinct().count()
    except Exception:
        ticks = session.query(ThemeTick.ts).distinct().count()
    from db.models import AttentionItem
    em = (session.query(AttentionItem).filter(AttentionItem.kind == "emerging", AttentionItem.ts >= now - 7 * DAY)
          .order_by(AttentionItem.ts.desc()).limit(8).all())
    return {"narratives": out, "hours": ticks, "needed": HISTORY_MIN_TICKS,
            "emerging": [{"title": e.title, "brands": e.brand, "words": e.source, "size": e.size, "ts": e.ts} for e in em]}


def for_titles(session, titles):
    """{title: [labels]} for story pages — only cached tags, never a new AI call."""
    rows = session.query(ThemeTag).filter(ThemeTag.title.in_([t[:300] for t in titles if t])).all() if titles else []
    got = {r.title: json.loads(r.themes or "[]") for r in rows}
    return {t: [LABELS[k] for k in (got.get(t[:300]) if t[:300] in got else tag_rules(t))] for t in titles if t}


# ─────────────────────────────────────────────
# EMERGING NARRATIVES — let the data find themes our 7 don't cover (test, recorded only)
#   Headlines that no narrative recognises are compared across DIFFERENT brands: two stories that share
#   2+ distinctive words are linked; a group spanning 2+ brands is an emerging narrative. Named by the
#   AI once (cached), else by its shared words. Recorded in the attention layer.
# ─────────────────────────────────────────────
EMERGE_MIN_SHARED = 2
_EM_EXTRA_STOP = set("""says said after over amid with from into about their they will would could
customers customer fans fan people menu item items brand brands chain company new news report reports
online viral internet video week today year""".split())


def _em_words(title, brand):
    from engine.discovery import _STOP
    from engine.everyday import BUZZ_GENERIC
    bw = set(re.findall(r"[a-z]+", (brand or "").lower()))
    return {w.rstrip("s") for w in re.findall(r"[a-z][a-z'-]{3,}", (title or "").lower().replace("’", "'"))
            if w not in _STOP and w not in BUZZ_GENERIC and w not in bw and w not in _EM_EXTRA_STOP}


def emerging(session, rows, tags):
    """Groups of untagged stories across 2+ brands sharing 2+ distinctive words → [{name, words, brands, titles, size}]."""
    from engine.everyday import brand_in
    items = []
    for r in rows:
        if tags.get(r.get("title")) or (r.get("score") or 0) < 8:
            continue
        b = (r.get("company") or brand_in([r.get("title") or ""]) or "").strip()
        if not b:
            continue
        items.append({"title": r["title"], "brand": b, "w": _em_words(r["title"], b),
                      "size": r.get("weighted") or r.get("score") or 0})
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i]["brand"].lower() != items[j]["brand"].lower() and len(items[i]["w"] & items[j]["w"]) >= EMERGE_MIN_SHARED:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(items[i])
    out = []
    for g in groups.values():
        brands = sorted({x["brand"] for x in g}, key=str.lower)
        if len(brands) < 2:
            continue
        common = set.intersection(*[x["w"] for x in g]) if len(g) > 1 else set()
        words = sorted(common or set.union(*[x["w"] for x in g]))[:4]
        out.append({"words": words, "brands": brands, "titles": [x["title"] for x in g][:4],
                    "size": round(sum(x["size"] for x in g), 1)})
    return out


def _name_emerging(words, titles):
    got = ask_json('These US food & drink headlines share a theme. Name it in 2-5 plain words, like a news '
                   'narrative (e.g. "seed oil fears", "tipping backlash"). Headlines:\n' +
                   "\n".join("- " + t[:160] for t in titles[:4]) + '\nAnswer JSON only: {"name": "..."}', 30)
    n = (got or {}).get("name")
    return n.strip()[:60] if isinstance(n, str) and n.strip() else " · ".join(words) or "unnamed theme"


def record_emerging(session, rows, tags):
    from db.models import AttentionItem
    found = emerging(session, rows, tags)
    for e in found:
        key = "em:" + "|".join(sorted(b.lower() for b in e["brands"]))[:110]
        old = session.query(AttentionItem).filter(AttentionItem.key == key).first()
        if old:
            old.size = max(old.size or 0, e["size"])
            continue
        name = _name_emerging(e["words"], e["titles"])
        session.add(AttentionItem(ts=time.time(), kind="emerging", brand=", ".join(e["brands"])[:60],
                                  title=f"{name} — " + " | ".join(e["titles"])[:420], size=e["size"],
                                  source=" · ".join(e["words"])[:80], key=key))
        print(f"🌱 Emerging narrative: {name} ({', '.join(e['brands'][:5])})")
    session.commit()
    return found


# ─────────────────────────────────────────────
# EPIDEMIC CURVE — where is a narrative in its life? (test, recorded only)
#   A narrative's strength works like the number of "infected": it rises, peaks, fades. The running total
#   (strength × hours) is fitted with a logistic curve, the simplest epidemic model: its midpoint is the
#   peak of the strength. Needs ≥ 24 hourly readings and a strength that reached RISE_MIN.
# ─────────────────────────────────────────────
import math


def phase(series):
    """series: [[ts, strength], …] hourly → {phase, peak_eta_h, growth} or {phase: 'too little data'}."""
    pts = [(t, max(0.0, v)) for t, v in series]
    if len(pts) < 24 or max(v for _, v in pts) < RISE_MIN:
        return {"phase": "too little data"}
    last6 = sum(v for _, v in pts[-6:]) / 6
    prev6 = sum(v for _, v in pts[-12:-6]) / 6
    growth = round((last6 + 1) / (prev6 + 1), 2)
    t0 = pts[0][0]
    hours = [(t - t0) / 3600 for t, _ in pts]
    cum, c = [], 0.0
    for v in (v for _, v in pts):
        c += v
        cum.append(c)
    now_c, now_h = cum[-1], hours[-1]
    best = None
    for i in range(30):                       # K from 1.05× to ~20× today's running total
        K = now_c * 1.05 * (1.11 ** i)
        xs, ys = [], []
        for h, cv in zip(hours, cum):
            if 0 < cv < K:
                xs.append(h)
                ys.append(math.log(K / cv - 1))
        if len(xs) < 12:
            continue
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 0:
            continue
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        ss = sum((y - my) ** 2 for y in ys) or 1e-9
        res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
        r2 = 1 - res / ss
        if slope < 0 and (best is None or r2 > best[0]):
            best = (r2, -slope, mx - my / slope)   # y = 0 where C = K/2: the curve's midpoint = the peak
    eta = None
    if best and best[0] >= 0.8:
        eta = round(best[2] - now_h, 1)       # hours until the fitted peak (negative = already past)
    if growth >= 1.2:
        ph = "growing"                        # early in a rise the fit can't place the peak reliably
        eta = eta if eta is not None and eta > 0 else None
    elif growth <= 0.8:
        ph = "fading"
    elif eta is not None and abs(eta) <= 12:
        ph = "near peak"
    else:
        ph = "steady"
    return {"phase": ph, "peak_eta_h": eta, "growth": growth}
