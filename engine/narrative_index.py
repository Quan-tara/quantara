"""
Food Sector Narrative Risk Index — Data Collector
===================================================
Runs every 15 minutes. Pulls from:
  - RSS feeds (food-safety outlets, FDA recalls, Google News searches in
    US / UK / Ireland / Australia, BBC + Guardian business)
  - openFDA enforcement API (recent recalls, Class I weighted)
  - GDELT (geographic spread + source diversity, last 24h)

Pipeline:
  1. Pull articles (last 72h only), keep the food-safety relevant ones.
  2. Group copies of the same story (syndicated across outlets) into one
     "story", so one recall covered by 30 TV stations counts once.
  3. Compute eight components (0-100), each scored against its own rolling
     7-day median (normal ≈ 20, 2× normal ≈ 60, 4× normal = 100), combine
     into two axes, then the index. Raw counts are stored in meta.raw so
     future runs can build the baseline.
  4. Store one tick in index_ticks. The `headlines` column holds a JSON
     payload: {"v": 2, "stories": [...], "components": {...}, "meta": {...}}.
     Delivery Pulse ticks share the table and leave `headlines` NULL.

Index formula:
  Intensity = 0.25·Volume + 0.25·Severity + 0.15·Negativity + 0.35·Regulatory
  Diffusion = 0.30·Sources + 0.25·Geography + 0.25·Sector breadth + 0.20·Persistence
  Final     = 0.45·Intensity + 0.55·Diffusion
"""

import sys
import re
import json
import math
import time
import calendar
from datetime import datetime, timedelta, timezone

import requests
import feedparser
from db.database import SessionLocal
from db.models import IndexTick

# Make print() reach Render's log immediately (stdout is buffered in containers)
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass


# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────

COLLECT_EVERY_SECS = 15 * 60
ARTICLE_WINDOW_H   = 72        # ignore articles older than this
FEED_TIMEOUT       = 12        # seconds per feed
MAX_PER_FEED       = 40        # articles per feed
MAX_STORIES        = 8         # stories shown on the page
UA = {"User-Agent": "Mozilla/5.0 (compatible; QuantaraBot/1.0; +https://quantara-tech.me)"}


# ─────────────────────────────────────────────
# FEEDS — (url, region reported from)
# ─────────────────────────────────────────────

def _gn(q, region):
    """Google News search feed for a query in a given edition."""
    ed = {"US": ("en-US", "US", "US:en"), "UK": ("en-GB", "GB", "GB:en"),
          "IE": ("en-IE", "IE", "IE:en"), "AU": ("en-AU", "AU", "AU:en")}[region]
    return (f"https://news.google.com/rss/search?q={q}+when:3d"
            f"&hl={ed[0]}&gl={ed[1]}&ceid={ed[2]}")

RSS_FEEDS = [
    # Food-safety specialists
    ("https://www.foodsafetynews.com/feed/", "US"),
    ("https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/recalls/rss.xml", "US"),
    # Google News searches — several angles and editions
    (_gn("food+recall+contamination", "US"), "US"),
    (_gn("salmonella+OR+listeria+OR+%22e.+coli%22+outbreak", "US"), "US"),
    (_gn("food+recall+OR+outbreak", "UK"), "UK"),
    (_gn("food+allergy+alert+recall", "UK"), "UK"),
    (_gn("food+safety+recall", "IE"), "EU"),
    (_gn("food+recall", "AU"), "AU"),
    # General business news (catches market impact)
    ("https://feeds.bbci.co.uk/news/business/rss.xml", "UK"),
    ("https://www.theguardian.com/business/rss", "UK"),
]


# ─────────────────────────────────────────────
# VOCABULARY
# ─────────────────────────────────────────────

# A hazard term makes an article relevant on its own
HAZARD_TERMS = [
    "salmonella", "listeria", "e. coli", "e.coli", "ecoli", "botulism", "botulinum",
    "norovirus", "hepatitis a", "cyclospora", "campylobacter", "cronobacter",
    "shigella", "vibrio", "foodborne", "food poisoning", "food-borne",
    "contamination", "contaminated", "undeclared", "allergen", "allergy alert",
    "food fraud", "adulterat", "aflatoxin", "melamine", "foreign matter",
    "metal fragments", "plastic pieces", "glass fragments", "lead levels",
]

# A recall/outbreak term needs a food term to count (so car recalls don't)
EVENT_TERMS = ["recall", "outbreak", "withdrawn", "withdrawal", "safety alert",
               "public health alert", "do not eat", "sickened"]

# Severity amplifiers
SEVERITY_TERMS = {
    "death": 3, "deaths": 3, "died": 3, "dies": 3, "fatal": 3, "killed": 3,
    "hospitali": 2, "intensive care": 2, "kidney failure": 2, "miscarriage": 2,
    "children": 1, "infant": 1, "baby": 1, "class i": 2, "nationwide": 1,
    "multistate": 1, "multi-state": 1, "criminal": 2, "investigation": 1,
    "cover-up": 2, "negligence": 1, "class action": 1, "lawsuit": 1,
    "public health emergency": 3, "outbreak": 1,
}

# Industry-wide language (diffusion signal)
SPREAD_TERMS = ["sector-wide", "industry-wide", "multiple brands", "several brands",
                "widespread", "across the industry", "nationwide", "europe-wide",
                "international recall", "global recall", "multiple countries",
                "other companies", "competitors", "precautionary recall",
                "supply chain", "multistate", "multi-state", "expanded recall",
                "recall expanded", "expands recall"]

# Food categories — for sector breadth and relevance
CATEGORIES = {
    "produce":      ["lettuce", "spinach", "greens", "salad", "cucumber", "tomato", "onion",
                     "sprouts", "jalape", "pepper", "cantaloupe", "melon", "fruit",
                     "vegetable", "berries", "strawberr", "produce", "herbs", "cilantro"],
    "meat":         ["beef", "pork", "chicken", "poultry", "turkey", "meat", "sausage",
                     "ham", "deli", "bacon", "jerky", "hot dog"],
    "seafood":      ["fish", "seafood", "salmon", "tuna", "oyster", "shrimp", "shellfish",
                     "clam", "mussel", "crab"],
    "dairy":        ["cheese", "milk", "dairy", "yogurt", "yoghurt", "ice cream",
                     "butter", "cream"],
    "eggs":         ["egg"],
    "infant":       ["infant formula", "baby food", "formula"],
    "pet food":     ["pet food", "dog food", "cat food", "pet treat", "dog treat",
                     "kibble", "raw pet"],
    "bakery/grain": ["bread", "flour", "cereal", "bakery", "pasta", "rice", "granola",
                     "cookie", "cake", "cracker"],
    "snacks":       ["chocolate", "candy", "snack", "chips", "nuts", "peanut", "almond",
                     "pistachio", "confection", "cookies"],
    "beverages":    ["juice", "drink", "beverage", "water", "tea", "coffee", "smoothie"],
    "spices":       ["spice", "cinnamon", "seasoning", "sauce", "dressing", "condiment"],
    "prepared":     ["ready-to-eat", "ready meal", "frozen", "sandwich", "meal kit",
                     "soup", "dip", "hummus", "salsa"],
    "supplements":  ["supplement", "protein powder", "vitamin"],
}
FOOD_TERMS = ["food", "foods", "meal", "eat", "restaurant", "grocery", "supermarket",
              "fda", "usda", "fsis", "food standards agency", "fsai", "efsa"] + \
             [kw for kws in CATEGORIES.values() for kw in kws]

NEG_WORDS = {
    "death", "deaths", "fatal", "fatality", "fatalities", "killed", "kill", "died",
    "serious", "severe", "dangerous", "emergency", "crisis", "outbreak", "outbreaks",
    "contaminated", "contamination", "poisoning", "sickened", "sick", "hospitalized",
    "hospitalised", "illness", "illnesses", "ill", "harm", "harmful", "recall",
    "recalled", "recalls", "warning", "alert", "ban", "banned", "unsafe", "risk",
    "threat", "toxic", "deadly", "widespread", "massive", "critical", "urgent",
    "fears", "scare", "lawsuit", "failure", "failed",
}

# Words that carry no "which story is this" information — ignored for grouping
GENERIC = set("""
a an the and or of for to in on at by with from over due because after amid as is are
was were be been being its it this that these those their his her new more than about
into up out says said say report reports reported issues issued issue announces
announced warns warned urges urged amid possible potential concerns concern risk risks
food foods product products recall recalls recalled recalling voluntary voluntarily
nationwide salmonella listeria monocytogenes coli e outbreak outbreaks contamination
contaminated fda usda fsis cdc health public alert alerts safety news update latest
illness illnesses sick sickened linked tied pounds lbs nearly over under some sold
stores store us uk across states state company companies brand brands
dead death deaths died dies two three four five dozen dozens people hospitalized
hospitalised hospitalizes hospitalization investigation investigating underway sickens
sicken cases case confirmed officials authorities warning warnings
""".split())

SYNONYMS = {"cat": "pet", "cats": "pet", "dog": "pet", "dogs": "pet", "pets": "pet",
            "kibble": "pet", "eggs": "egg", "cucumbers": "cucumber",
            "tomatoes": "tomato", "onions": "onion", "cheeses": "cheese"}


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

# Terms matched as word prefixes (everything else must be a whole word, plural ok)
_PREFIX_TERMS = {"jalape", "strawberr", "confection", "hospitali", "adulterat", "botul"}
_RX_CACHE = {}


def _rx(terms):
    key = id(terms)
    if key not in _RX_CACHE:
        parts = []
        for t in sorted(set(terms), key=len, reverse=True):
            esc = re.escape(t)
            parts.append(r"\b" + esc + ("" if t in _PREFIX_TERMS else r"(?:s|es)?\b"))
        _RX_CACHE[key] = re.compile("|".join(parts))
    return _RX_CACHE[key]


def _has_any(text, terms):
    return _rx(terms).search(text) is not None


def _clean_title(title, source):
    """Google News titles end in ' - Outlet'; strip it."""
    title = re.sub(r"\s+", " ", title or "").strip()
    if source and title.endswith(" - " + source):
        title = title[: -len(" - " + source)].strip()
    return title


def _story_tokens(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    out = set()
    for w in words:
        w = SYNONYMS.get(w, w)
        if w in GENERIC or len(w) < 3 or w.isdigit():
            continue
        if len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        out.add(w)
    return out


def _hazards(text):
    keys = {"salmonella": "salmonella", "listeria": "listeria", "e. coli": "ecoli",
            "e.coli": "ecoli", "ecoli": "ecoli", "botul": "botulism",
            "norovirus": "norovirus", "hepatitis a": "hepa", "cyclospora": "cyclospora",
            "undeclared": "allergen", "allergen": "allergen", "allergy": "allergen",
            "campylobacter": "campylobacter", "cronobacter": "cronobacter"}
    return {v for k, v in keys.items() if k in text}


def _same_story(a, b):
    """Two articles are the same story if hazards agree and their distinctive
    words overlap strongly (or one's distinctive words are contained in the other's)."""
    if a["hazards"] and b["hazards"] and not (a["hazards"] & b["hazards"]):
        return False
    ta, tb = a["tokens"], b["tokens"]
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    if inter == 0:
        return False
    jacc = inter / len(ta | tb)
    contained = inter == min(len(ta), len(tb))
    return (jacc >= 0.34
            or (inter >= 2 and jacc >= 0.2)
            or (contained and min(len(ta), len(tb)) <= 3 and max(len(ta), len(tb)) <= 6))


def _negativity(text):
    words = re.findall(r"[a-z]+", text)
    hits = sum(1 for w in words if w in NEG_WORDS)
    return min(1.0, hits * 0.12)


_SEV_RX = {k: re.compile(r"\b" + re.escape(k) + ("" if k in _PREFIX_TERMS else r"\b"))
           for k in SEVERITY_TERMS}


def _severity(text):
    pts = sum(w for k, w in SEVERITY_TERMS.items() if _SEV_RX[k].search(text))
    return min(1.0, pts / 6.0)


def _categories(text):
    return {cat for cat, kws in CATEGORIES.items() if _has_any(text, kws)}


def _entry_ts(entry):
    for k in ("published_parsed", "updated_parsed"):
        t = entry.get(k)
        if t:
            try:
                return float(calendar.timegm(t))
            except Exception:
                pass
    return None


# ─────────────────────────────────────────────
# RSS PULL
# ─────────────────────────────────────────────

def pull_rss_articles():
    """Returns (relevant_articles, stats)."""
    now = time.time()
    cutoff = now - ARTICLE_WINDOW_H * 3600
    articles, seen_titles = [], set()
    total, ok_feeds = 0, 0

    for feed_url, region in RSS_FEEDS:
        short = feed_url.split("?")[0][:70]
        try:
            resp = requests.get(feed_url, timeout=FEED_TIMEOUT, headers=UA)
            if resp.status_code != 200:
                print(f"⚠️ Feed {short} → HTTP {resp.status_code}")
                continue
            feed = feedparser.parse(resp.content)
            entries = feed.entries[:MAX_PER_FEED]
            if not entries:
                print(f"⚠️ Feed {short} → 0 entries")
                continue
            ok_feeds += 1
            feed_name = (feed.feed.get("title") or "").strip()

            for e in entries:
                src = ""
                if e.get("source") and hasattr(e.source, "get"):
                    src = (e.source.get("title") or "").strip()
                src = src or feed_name or "Unknown"
                title = _clean_title(e.get("title", ""), src)
                if not title:
                    continue
                total += 1
                ts = _entry_ts(e)
                if ts and ts < cutoff:
                    continue
                key = (title.lower(), src.lower())
                if key in seen_titles:          # same article from two feeds
                    continue
                seen_titles.add(key)

                summary = re.sub(r"<[^>]+>", " ", e.get("summary", "") or "")
                text = (title + " " + summary).lower()
                relevant = _has_any(text, HAZARD_TERMS) or (
                    _has_any(text, EVENT_TERMS) and _has_any(text, FOOD_TERMS))
                if not relevant:
                    continue

                articles.append({
                    "title":      title,
                    "source":     src,
                    "url":        e.get("link", ""),
                    "ts":         ts or now,
                    "region":     region,
                    "tokens":     _story_tokens(title),
                    "hazards":    _hazards(text),
                    "categories": _categories(text),
                    "severity":   _severity(text),
                    "negativity": _negativity(text),
                    "spread":     _has_any(text, SPREAD_TERMS),
                })
        except requests.exceptions.Timeout:
            print(f"⚠️ Feed timeout: {short}")
        except Exception as ex:
            print(f"⚠️ Feed error {short}: {ex}")

    print(f"📰 {ok_feeds}/{len(RSS_FEEDS)} feeds OK | {len(articles)} relevant / {total} total articles")
    return articles, {"feeds_ok": ok_feeds, "feeds_total": len(RSS_FEEDS),
                      "articles_total": total, "articles_relevant": len(articles)}


# ─────────────────────────────────────────────
# STORY GROUPING
# ─────────────────────────────────────────────

def group_stories(articles):
    """Greedy clustering: each article joins the first story it matches."""
    stories = []
    for a in sorted(articles, key=lambda x: x["ts"]):      # oldest first
        for s in stories:
            if any(_same_story(a, m) for m in s["members"]):
                s["members"].append(a)
                break
        else:
            stories.append({"members": [a]})

    out = []
    for s in stories:
        m = s["members"]
        outlets = sorted({x["source"] for x in m})
        regions = sorted({x["region"] for x in m})
        severity = max(x["severity"] for x in m)
        negativity = sum(x["negativity"] for x in m) / len(m)
        cats = set().union(*(x["categories"] for x in m))
        spread = any(x["spread"] for x in m)
        # headline to show: the most widely-worded (longest token set) recent one
        lead = max(m, key=lambda x: (len(x["tokens"]), x["ts"]))
        n_out = len(outlets)
        score = severity * 0.5 + negativity * 0.2 + min(1.0, math.log2(1 + n_out) / 4) * 0.6
        impact = max(1, min(5, round(1 + severity * 2 + math.log2(1 + n_out))))
        # CRISIS needs real harm (deaths, hospitalisations, Class I) AND pickup
        if severity >= 0.5 and n_out >= 3:
            tier, tier_label = "t1", "CRISIS"
        elif n_out >= 5 or severity >= 0.5:
            tier, tier_label = "t2", "STRESS"
        else:
            tier, tier_label = "t3", "WATCH"
        out.append({
            "title":      lead["title"],
            "source":     lead["source"],
            "url":        lead["url"],
            "ts":         max(x["ts"] for x in m),
            "first_ts":   min(x["ts"] for x in m),
            "outlets":    n_out,
            "articles":   len(m),
            "regions":    regions,
            "categories": sorted(cats),
            "severity":   round(severity, 3),
            "negativity": round(negativity, 3),
            "spread":     spread,
            "score":      round(score, 4),
            "impact":     impact,
            "tier":       tier,
            "tier_label": tier_label,
            "all_outlets": outlets,
        })
    out.sort(key=lambda s: (s["score"], s["ts"]), reverse=True)
    return out


# ─────────────────────────────────────────────
# FDA — recent enforcement reports (openFDA)
# ─────────────────────────────────────────────

def fetch_fda_signal():
    """Food enforcement reports in the last 30 days. Returns dict, zeros on error."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    url = ("https://api.fda.gov/food/enforcement.json?search=report_date:["
           f"{start:%Y%m%d}+TO+{end:%Y%m%d}]&limit=1000")
    try:
        r = requests.get(url, timeout=15, headers=UA)
        if r.status_code == 404:               # openFDA: no matches
            return {"events": 0, "class1_events": 0}
        if r.status_code != 200:
            print(f"⚠️ FDA API returned {r.status_code}")
            return {"events": 0, "class1_events": 0}
        results = r.json().get("results", [])
        events = {x.get("event_id") or x.get("recall_number") for x in results}
        class1 = {x.get("event_id") or x.get("recall_number") for x in results
                  if (x.get("classification") or "").strip() == "Class I"}
        return {"events": len(events), "class1_events": len(class1)}
    except Exception as e:
        print(f"⚠️ FDA API error: {e}")
        return {"events": 0, "class1_events": 0}


# ─────────────────────────────────────────────
# GDELT — geographic spread + source diversity (last 24h)
# ─────────────────────────────────────────────

GDELT_API = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=(%22food%20recall%22%20OR%20%22food%20contamination%22%20OR%20%22food%20poisoning%22"
    "%20OR%20salmonella%20OR%20listeria)"
    "&mode=artlist&maxrecords=250&format=json&timespan=24h"
)


def fetch_gdelt_signal():
    try:
        r = requests.get(GDELT_API, timeout=20, headers=UA)
        if r.status_code != 200:
            print(f"⚠️ GDELT returned {r.status_code}")
            return {"country_count": 0, "source_count": 0, "ok": False}
        try:
            data = r.json()
        except ValueError:
            print(f"⚠️ GDELT non-JSON reply: {r.text[:80]!r}")
            return {"country_count": 0, "source_count": 0, "ok": False}
        arts = data.get("articles", [])
        countries = {a["sourcecountry"] for a in arts if a.get("sourcecountry")}
        sources = {a["domain"] for a in arts if a.get("domain")}
        print(f"🌍 GDELT: {len(countries)} countries, {len(sources)} sources, {len(arts)} articles")
        return {"country_count": len(countries), "source_count": len(sources), "ok": True}
    except Exception as e:
        print(f"⚠️ GDELT error: {e}")
        return {"country_count": 0, "source_count": 0, "ok": False}


# ─────────────────────────────────────────────
# BASELINE — rolling 7-day median of each raw metric
# ─────────────────────────────────────────────
# Every component is scored against what is NORMAL for that metric,
# not against a fixed limit. A typical day scores ~20; double the usual
# level ~60; four times the usual level 100.

BASELINE_DAYS        = 7
BASELINE_MIN_SAMPLES = 4      # fewer than this → today's level is "normal"
BASELINE_MAX_ROWS    = 400    # ~4 days at 15-min ticks; enough for a stable median

# Floors stop tiny baselines (e.g. 0 Class I recalls) from exploding the ratio
RAW_FLOORS = {
    "stories": 5, "articles": 10, "severity": 0.1, "negativity": 0.1,
    "fda_class1": 2, "fda_events": 5, "outlets": 5, "top_story_outlets": 3,
    "gdelt_sources": 10, "gdelt_countries": 3, "regions": 2,
    "categories": 3, "spread_share": 0.05, "story_span_h": 6,
}


def get_baselines(session) -> dict:
    """Median of each raw metric over previous FSNI ticks (last 7 days)."""
    import sqlalchemy as sa
    cutoff_ts = time.time() - BASELINE_DAYS * 24 * 3600
    samples = {k: [] for k in RAW_FLOORS}
    try:
        rows = session.execute(
            sa.text("SELECT headlines FROM index_ticks "
                    "WHERE ts >= :cutoff AND headlines IS NOT NULL "
                    "ORDER BY ts DESC LIMIT :lim"),
            {"cutoff": cutoff_ts, "lim": BASELINE_MAX_ROWS}
        ).fetchall()
        for (h,) in rows:
            try:
                raw = (json.loads(h) or {}).get("meta", {}).get("raw")
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            for k in samples:
                v = raw.get(k)
                if isinstance(v, (int, float)):
                    samples[k].append(float(v))
    except Exception as e:
        print(f"⚠️ Baseline query error: {e}")

    out = {}
    for k, vals in samples.items():
        if len(vals) >= BASELINE_MIN_SAMPLES:
            vals.sort()
            n = len(vals)
            out[k] = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    out["_samples"] = max((len(v) for v in samples.values()), default=0)
    return out


def _rel(raw, base, key):
    """Score 0-1 for a raw metric relative to its baseline.
    ratio 1 (normal) → 0.20, 1.5 → 0.43, 2 → 0.60, 3 → 0.83, 4+ → 1.0"""
    cur = raw.get(key)
    if cur is None:
        return None
    b = base.get(key, cur)                       # no history yet → today is normal
    b = max(b, RAW_FLOORS[key])
    r = max(0.0, cur) / b
    if r <= 1:
        return 0.2 * r
    return min(1.0, 0.2 + 0.4 * math.log2(r))


def _mean(*xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


# ─────────────────────────────────────────────
# MAIN INDEX CALCULATION
# ─────────────────────────────────────────────

def _pct(x):
    return round(max(0.0, min(1.0, x)) * 100, 1)


def calculate_narrative_index() -> dict:
    session = SessionLocal()
    try:
        articles, stats = pull_rss_articles()
        stories = group_stories(articles)
        print(f"🧩 {len(articles)} articles grouped into {len(stories)} distinct stories")

        fda = fetch_fda_signal()
        print(f"🏛️ FDA last 30d: {fda['events']} recall events, {fda['class1_events']} Class I")
        gdelt = fetch_gdelt_signal()

        # ── Raw metrics for this run (stored, and used for future baselines) ──
        n_st = len(stories)
        top_sev = sorted((s["severity"] for s in stories), reverse=True)[:5]
        by_reach = sorted(stories, key=lambda s: s["outlets"], reverse=True)[:5]
        cats = set().union(*(s["categories"] for s in stories)) if stories else set()
        raw = {
            "stories":           n_st,
            "articles":          len(articles),
            "severity":          round(sum(top_sev) / 5, 4) if top_sev else 0.0,
            "negativity":        round(sum(a["negativity"] for a in articles) / len(articles), 4) if articles else 0.0,
            "fda_class1":        fda["class1_events"],
            "fda_events":        fda["events"],
            "outlets":           len({a["source"] for a in articles}),
            "top_story_outlets": by_reach[0]["outlets"] if by_reach else 0,
            "regions":           len({a["region"] for a in articles}),
            "categories":        len(cats),
            "spread_share":      round(sum(1 for s in stories if s["spread"]) / n_st, 4) if n_st else 0.0,
            # how long the most-covered stories have been running (hours)
            "story_span_h":      round(_mean(*[(s["ts"] - s["first_ts"]) / 3600 for s in by_reach]), 2),
        }
        if gdelt["ok"]:
            raw["gdelt_sources"]   = gdelt["source_count"]
            raw["gdelt_countries"] = gdelt["country_count"]

        base = get_baselines(session)
        n_base = base.get("_samples", 0)
        warm = n_base >= BASELINE_MIN_SAMPLES
        print(f"📐 Baseline: {n_base} previous runs" + ("" if warm else " (warming up — today = normal)"))

        # ── Components (0-1), each relative to its own normal ──
        R = lambda k: _rel(raw, base, k)
        volume      = _mean(R("stories"), R("articles"))
        severity    = R("severity")
        negativity  = R("negativity")
        regulatory  = 0.7 * R("fda_class1") + 0.3 * R("fda_events")
        sources     = _mean(R("outlets"), R("gdelt_sources"), R("top_story_outlets"))
        geography   = R("gdelt_countries") if gdelt["ok"] else R("regions")
        breadth     = _mean(R("categories"), R("spread_share"))
        persistence = R("story_span_h")

        intensity_axis = (0.25 * volume + 0.25 * severity + 0.15 * negativity
                          + 0.35 * regulatory) * 100
        diffusion_axis = (0.30 * sources + 0.25 * geography + 0.25 * breadth
                          + 0.20 * persistence) * 100
        final_index = round(min(100.0, max(0.0,
                            intensity_axis * 0.45 + diffusion_axis * 0.55)), 2)

        def vs(k, fmt="{:.0f}"):
            if k not in raw:
                return "n/a"
            b = base.get(k)
            return fmt.format(raw[k]) + (f" vs normal {fmt.format(b)}" if b is not None else " (baseline building)")

        components = {
            "volume":      {"value": _pct(volume),      "detail": f"stories {vs('stories')} · articles {vs('articles')}"},
            "severity":    {"value": _pct(severity),    "detail": f"top-5 severity {vs('severity', '{:.2f}')}"},
            "negativity":  {"value": _pct(negativity),  "detail": f"tone {vs('negativity', '{:.2f}')}"},
            "regulatory":  {"value": _pct(regulatory),  "detail": f"FDA 30d Class I {vs('fda_class1')} · all {vs('fda_events')}"},
            "sources":     {"value": _pct(sources),     "detail": f"outlets {vs('outlets')} · top story {vs('top_story_outlets')} · GDELT {vs('gdelt_sources')}"},
            "geography":   {"value": _pct(geography),   "detail": f"countries {vs('gdelt_countries')}" if gdelt["ok"] else f"feed regions {vs('regions')}"},
            "breadth":     {"value": _pct(breadth),     "detail": f"categories {vs('categories')}: " + (", ".join(sorted(cats)) or "none")},
            "persistence": {"value": _pct(persistence), "detail": f"top stories running {vs('story_span_h', '{:.1f}')} h"},
        }
        rss_outlets = raw["outlets"]

        top = [{k: v for k, v in s.items() if k != "all_outlets"} | {"outlet_names": s["all_outlets"][:6]}
               for s in stories[:MAX_STORIES]]

        print(f"📊 Intensity: {intensity_axis:.1f} | Diffusion: {diffusion_axis:.1f} | Final: {final_index}")
        for s in top[:3]:
            print(f"   • [{s['outlets']} outlets] {s['title'][:90]}")

        return {
            "value":           final_index,
            "intensity_score": round(intensity_axis, 2),
            "diffusion_score": round(diffusion_axis, 2),
            "payload": {
                "v": 2,
                "stories": top,
                "components": components,
                "meta": {**stats, "stories_total": n_st, "outlets": rss_outlets,
                         "gdelt_ok": gdelt["ok"], "baseline_runs": n_base, "raw": raw},
            },
        }
    finally:
        session.close()


# ─────────────────────────────────────────────
# STORE TICK
# ─────────────────────────────────────────────

def store_index_tick(scores: dict):
    session = SessionLocal()
    try:
        tick = IndexTick(
            value           = scores["value"],
            volatility      = 0.0,
            ts              = time.time(),
            intensity_score = scores["intensity_score"],
            diffusion_score = scores["diffusion_score"],
            headlines       = json.dumps(scores["payload"]),
        )
        session.add(tick)
        session.commit()
        print(f"✅ Tick stored: {scores['value']} (I:{scores['intensity_score']} "
              f"D:{scores['diffusion_score']}) [{len(scores['payload']['stories'])} stories]")
    except Exception as e:
        session.rollback()
        print(f"❌ Tick store error: {e}")
    finally:
        session.close()


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

def run_collector_loop():
    """Start the collector in a background thread. Call from app startup."""
    import threading

    def _loop():
        print("🚀 Narrative Index Collector started")
        while True:
            try:
                store_index_tick(calculate_narrative_index())
            except Exception as e:
                print(f"❌ Collector error: {e}")
            time.sleep(COLLECT_EVERY_SECS)

    threading.Thread(target=_loop, daemon=True).start()


# ─────────────────────────────────────────────
# COMPATIBILITY SHIM — used by exchange pricing / P&L. Do not change.
# ─────────────────────────────────────────────

def get_risk_index() -> float:
    import sqlalchemy as sa
    session = SessionLocal()
    try:
        row = session.execute(
            sa.text("SELECT value FROM index_ticks ORDER BY ts DESC LIMIT 1")
        ).fetchone()
        return float(row[0]) if row else 10.0
    except Exception:
        return 10.0
    finally:
        session.close()
