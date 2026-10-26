"""
Food Sector Narrative Risk Index — Data Collector
===================================================
Runs every 15 minutes. Pulls from:
  - RSS feeds (major news outlets)
  - FDA recall API (regulatory response)
  - GDELT (geographic spread + source diversity)

Scores articles against Intensity and Diffusion axes.
Stores result in index_ticks table with both axis scores.

Index formula:
  Final = (Intensity × 0.45) + (Diffusion × 0.55)
"""

import time
import math
import requests
import feedparser
from datetime import datetime, timedelta
from textblob import TextBlob
from db.database import SessionLocal
from db.models import IndexTick


# ─────────────────────────────────────────────
# KEYWORD LISTS
# ─────────────────────────────────────────────

INTENSITY_TIER1 = [
    "food contamination", "foodborne illness", "outbreak", "salmonella",
    "listeria", "e. coli", "e.coli", "botulism", "norovirus",
    "food poisoning", "food fraud", "adulteration", "undeclared allergen",
    "food recall", "product recall"
]

INTENSITY_TIER2_AMPLIFIERS = [
    "deaths", "fatalities", "hospitalization", "children affected",
    "criminal investigation", "cover-up", "negligence", "regulatory penalty",
    "class action", "public health emergency"
]

DIFFUSION_TIER1 = [
    "sector-wide", "industry-wide", "multiple brands", "widespread",
    "across the industry", "consumer warning", "nationwide", "europe-wide",
    "global food safety", "international recall"
]

DIFFUSION_TIER2 = [
    "other companies", "competitors affected", "similar products",
    "precautionary recall", "related products", "supply chain failure",
    "food supply disruption", "broader concern"
]

ALL_FOOD_KEYWORDS = (
    INTENSITY_TIER1 + INTENSITY_TIER2_AMPLIFIERS +
    DIFFUSION_TIER1 + DIFFUSION_TIER2
)


# ─────────────────────────────────────────────
# RSS FEED SOURCES
# ─────────────────────────────────────────────

RSS_FEEDS = [
    # Global news
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "http://rss.cnn.com/rss/money_news_international.rss",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://www.theguardian.com/business/rss",
    # Food specific
    "https://www.foodsafetynews.com/feed/",
    "https://www.foodnavigator.com/rss/editorial.rss",
    "https://www.foodnavigator-usa.com/rss/editorial.rss",
    # Google News food safety search
    "https://news.google.com/rss/search?q=food+safety+recall+contamination&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=food+recall+outbreak&hl=en-GB&gl=GB&ceid=GB:en",
]


# ─────────────────────────────────────────────
# FDA RECALL API
# ─────────────────────────────────────────────

FDA_API_URL = (
    "https://api.fda.gov/food/enforcement.json"
    "?search=status:Ongoing&limit=10&sort=report_date:desc"
)


def fetch_fda_alerts() -> int:
    """Returns count of active FDA food enforcement actions. 0 on error."""
    try:
        r = requests.get(FDA_API_URL, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("meta", {}).get("results", {}).get("total", 0)
        return 0
    except Exception as e:
        print(f"⚠️ FDA API error: {e}")
        return 0


# ─────────────────────────────────────────────
# GDELT — geographic spread + source diversity
# ─────────────────────────────────────────────

GDELT_API = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=food%20recall%20OR%20food%20contamination%20OR%20food%20safety"
    "&mode=artlist&maxrecords=250&format=json&timespan=15min"
)


def fetch_gdelt_signal() -> dict:
    """
    Returns:
      country_count  — distinct countries reporting
      source_count   — distinct domains reporting
    Returns zeros on error or if GDELT is blocked.
    """
    try:
        r = requests.get(GDELT_API, timeout=15)
        if r.status_code != 200:
            return {"country_count": 0, "source_count": 0}
        data = r.json()
        articles = data.get("articles", [])
        countries = set()
        sources   = set()
        for a in articles:
            if a.get("sourcecountry"):
                countries.add(a["sourcecountry"])
            if a.get("domain"):
                sources.add(a["domain"])
        return {
            "country_count": len(countries),
            "source_count":  len(sources)
        }
    except Exception as e:
        print(f"⚠️ GDELT error (may be blocked in this env): {e}")
        return {"country_count": 0, "source_count": 0}


# ─────────────────────────────────────────────
# ARTICLE SCORING
# ─────────────────────────────────────────────

def score_article(title: str, summary: str) -> dict:
    """
    Score one article against intensity and diffusion keyword tiers.
    Returns dict with intensity_raw and diffusion_raw scores (0-1).
    """
    text = (title + " " + summary).lower()

    # Intensity scoring
    tier1_hits      = sum(1 for kw in INTENSITY_TIER1 if kw in text)
    amplifier_hits  = sum(1 for kw in INTENSITY_TIER2_AMPLIFIERS if kw in text)

    # Sentiment via TextBlob (-1 to +1, we want negative)
    blob = TextBlob(title + " " + summary)
    negativity = max(0, -blob.sentiment.polarity)  # 0 to 1, higher = more negative

    # Intensity raw: tier1 hits weighted most, amplifiers boost, sentiment adds
    intensity_raw = min(1.0, (
        (tier1_hits  * 0.15) +
        (amplifier_hits * 0.20) +
        (negativity  * 0.30)
    ))

    # Diffusion scoring
    diff1_hits = sum(1 for kw in DIFFUSION_TIER1 if kw in text)
    diff2_hits = sum(1 for kw in DIFFUSION_TIER2 if kw in text)

    diffusion_raw = min(1.0, (
        (diff1_hits * 0.20) +
        (diff2_hits * 0.15)
    ))

    return {
        "intensity_raw": intensity_raw,
        "diffusion_raw": diffusion_raw,
        "is_relevant":   tier1_hits > 0 or diff1_hits > 0
    }


# ─────────────────────────────────────────────
# PERSISTENCE — rolling window check
# ─────────────────────────────────────────────

def get_persistence_score(session) -> float:
    """
    Check how many of the last 7 days had elevated index (value > 20).
    Returns 0.0 to 1.0.
    """
    import sqlalchemy as sa
    cutoff_ts = time.time() - (7 * 24 * 3600)
    try:
        rows = session.execute(
            sa.text(
                "SELECT value FROM index_ticks "
                "WHERE ts >= :cutoff ORDER BY ts DESC"
            ),
            {"cutoff": cutoff_ts}
        ).fetchall()

        if not rows:
            return 0.0

        elevated = sum(1 for r in rows if r[0] > 20)
        return min(1.0, elevated / max(len(rows), 1))
    except Exception as e:
        print(f"⚠️ Persistence check error: {e}")
        return 0.0


# ─────────────────────────────────────────────
# MAIN INDEX CALCULATION
# ─────────────────────────────────────────────

def calculate_narrative_index() -> dict:
    """
    Pull all sources, score, combine into final index.
    Returns dict with value, intensity_score, diffusion_score.
    """
    session = SessionLocal()

    try:
        # ── 1. Pull and score RSS articles ──
        article_scores = []
        total_articles = 0

        for feed_url in RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:20]:  # max 20 per feed
                    title   = entry.get("title", "")
                    summary = entry.get("summary", "") or entry.get("description", "")
                    score   = score_article(title, summary)
                    if score["is_relevant"]:
                        article_scores.append(score)
                    total_articles += 1
            except Exception as e:
                print(f"⚠️ Feed error {feed_url[:50]}: {e}")

        relevant_count = len(article_scores)
        print(f"📰 {relevant_count} relevant articles from {total_articles} total")

        # ── 2. Aggregate article scores ──
        if article_scores:
            avg_intensity = sum(s["intensity_raw"] for s in article_scores) / len(article_scores)
            avg_diffusion = sum(s["diffusion_raw"] for s in article_scores) / len(article_scores)
        else:
            avg_intensity = 0.0
            avg_diffusion = 0.0

        # ── 3. FDA regulatory signal ──
        fda_count       = fetch_fda_alerts()
        # Normalize: 0 alerts = 0, 5+ alerts = 1.0
        fda_score       = min(1.0, fda_count / 5.0)
        print(f"🏛️ FDA active alerts: {fda_count} → score {fda_score:.2f}")

        # ── 4. GDELT geographic + source diversity ──
        gdelt           = fetch_gdelt_signal()
        country_score   = min(1.0, gdelt["country_count"] / 10.0)  # 10+ countries = max
        source_score    = min(1.0, gdelt["source_count"]  / 20.0)  # 20+ sources = max
        print(f"🌍 GDELT: {gdelt['country_count']} countries, {gdelt['source_count']} sources")

        # ── 5. Time persistence ──
        persistence     = get_persistence_score(session)
        print(f"⏳ Persistence score: {persistence:.2f}")

        # ── 6. INTENSITY AXIS (0-100) ──
        # Severity keywords:  40% of axis
        # Regulatory (FDA):   35% of axis
        # Sentiment:          25% of axis (baked into avg_intensity already)
        intensity_axis = (
            (avg_intensity * 0.65) +   # keyword severity + sentiment combined
            (fda_score     * 0.35)     # regulatory weight
        ) * 100

        # ── 7. DIFFUSION AXIS (0-100) ──
        # Source diversity:   30% of axis
        # Geographic spread:  25% of axis
        # Sector breadth:     25% of axis (from diffusion keywords in articles)
        # Time persistence:   20% of axis
        diffusion_axis = (
            (source_score    * 0.30) +
            (country_score   * 0.25) +
            (avg_diffusion   * 0.25) +
            (persistence     * 0.20)
        ) * 100

        # ── 8. FINAL INDEX ──
        final_index = (intensity_axis * 0.45) + (diffusion_axis * 0.55)
        final_index = round(min(100.0, max(0.0, final_index)), 2)

        print(f"📊 Intensity: {intensity_axis:.1f} | Diffusion: {diffusion_axis:.1f} | Final: {final_index}")

        return {
            "value":           final_index,
            "intensity_score": round(intensity_axis, 2),
            "diffusion_score": round(diffusion_axis, 2)
        }

    finally:
        session.close()


# ─────────────────────────────────────────────
# STORE TICK
# ─────────────────────────────────────────────

def store_index_tick(scores: dict):
    """Store calculated index scores in index_ticks table."""
    session = SessionLocal()
    try:
        tick = IndexTick(
            value           = scores["value"],
            volatility      = 0.0,
            ts              = time.time(),
            intensity_score = scores["intensity_score"],
            diffusion_score = scores["diffusion_score"]
        )
        session.add(tick)
        session.commit()
        print(f"✅ Tick stored: {scores['value']} (I:{scores['intensity_score']} D:{scores['diffusion_score']})")
    except Exception as e:
        session.rollback()
        print(f"❌ Tick store error: {e}")
    finally:
        session.close()


# ─────────────────────────────────────────────
# SCHEDULER — runs every 15 minutes
# ─────────────────────────────────────────────

def run_collector_loop():
    """Main loop. Call this from app startup."""
    import threading

    def _loop():
        print("🚀 Narrative Index Collector started")
        while True:
            try:
                scores = calculate_narrative_index()
                store_index_tick(scores)
            except Exception as e:
                print(f"❌ Collector error: {e}")
            time.sleep(15 * 60)  # 15 minutes

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


# ─────────────────────────────────────────────
# COMPATIBILITY SHIM
# ─────────────────────────────────────────────
# Replaces engine/index_provider.py get_risk_index()
# so existing settlement.py and pnl.py need no changes.

def get_risk_index() -> float:
    """
    Returns latest index value from DB.
    Falls back to 10.0 if no ticks exist yet.
    """
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
