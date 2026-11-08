"""
Everyday arena — relatable stories about brands and services people use
========================================================================
The game lists stories an ordinary person has an opinion on: price hikes, new fees,
a favourite product scrapped, an app or airline making customers furious, a brand in a
viral backlash. Not: recall notices, court filings, earnings.

A story qualifies when a headline has
  • a consumer anchor   — a known consumer brand, or everyday words (subscription,
                          customers, passengers, menu, app, prices…)
  • a human reaction    — anger, backlash, viral, boycott, price hike, scrapped, outage…
  • little jargon       — earnings, filings, regulators, recall notices count against
and passes the same safety filters as the brand arena (no crime, no private life, no
politics unless a company is the subject, no roundups).

Sources (all free): Google Trends daily trending searches (with their news items),
Google News searches phrased the way people complain, Guardian Money, BBC Business/Tech.
Each story gets a SECTOR (streaming, airlines, food & drink…) — sectors later get their
own indices, like the FSNI for food.
"""

import re
import time
import xml.etree.ElementTree as ET

import requests

from engine import narrative_index as ni
from engine import arenas as A

# ─────────────────────────────────────────────
# Vocabulary
# ─────────────────────────────────────────────

CONSUMER_BRANDS = [
    "netflix", "spotify", "disney+", "disney plus", "amazon prime", "prime video", "youtube", "hbo",
    "apple", "iphone", "samsung", "google", "gmail", "android", "instagram", "whatsapp", "facebook",
    "tiktok", "snapchat", "uber", "lyft", "airbnb", "booking.com", "ryanair", "easyjet", "lufthansa",
    "british airways", "united airlines", "delta", "american airlines", "southwest", "wizz air",
    "eurowings", "condor", "deutsche bahn", "mcdonald's", "mcdonalds", "burger king", "kfc",
    "starbucks", "subway", "domino's", "dominos", "pizza hut", "taco bell", "chipotle", "wendy's",
    "greggs", "pret", "costa", "coca-cola", "coke", "pepsi", "red bull", "cadbury", "nestle", "nestlé",
    "haribo", "milka", "walmart", "target", "costco", "aldi", "lidl", "tesco", "sainsbury's", "asda",
    "morrisons", "rewe", "edeka", "kaufland", "dm", "ikea", "primark", "h&m", "zara", "nike", "adidas",
    "puma", "shein", "temu", "tesla", "volkswagen", "vw", "bmw", "ticketmaster", "playstation", "xbox",
    "nintendo", "steam", "microsoft", "windows", "chatgpt", "openai", "amazon", "ebay", "paypal",
    "klarna", "revolut", "n26", "vodafone", "telekom", "o2", "verizon", "at&t", "t-mobile", "sky",
    "dhl", "royal mail", "deliveroo", "uber eats", "lieferando", "doordash", "instacart", "zalando",
    "lego", "barbie", "mattel", "peloton", "fitbit", "gymshark", "boots", "superdrug", "sephora",
    "ulta", "lush", "apple tv", "paramount", "peacock", "dazn", "crunchyroll", "duolingo", "tinder",
]

EVERYDAY_WORDS = [
    "subscription", "subscriptions", "subscribers", "customers", "shoppers", "passengers", "travellers",
    "travelers", "diners", "fans", "users", "parents", "drivers", "commuters", "menu", "recipe",
    "flavour", "flavor", "app", "update", "price", "prices", "fee", "fees", "charge", "charges",
    "ticket", "tickets", "flight", "flights", "store", "stores", "supermarket", "restaurant",
    "delivery", "account", "password", "ads", "advert", "streaming", "phone", "loyalty", "points",
    "refund", "queue", "self-checkout", "self checkout", "tipping", "tip", "baggage", "luggage",
    # German
    "abo", "kunden", "fahrgäste", "passagiere", "nutzer", "preis", "preise", "gebühr", "gebühren",
    "filiale", "filialen", "supermarkt", "lieferung", "speisekarte", "rezept", "flug", "flüge",
]

REACTION_WORDS = [
    "backlash", "furious", "fury", "outrage", "outraged", "angry", "anger", "slammed", "blasted",
    "fuming", "livid", "complain", "complaints", "complaining", "petition", "boycott", "boycotting",
    "viral", "divided", "row", "uproar", "criticism", "criticised", "criticized", "mocked",
    "ridiculed", "threaten to cancel", "cancel their", "cancelling", "canceling", "quit", "ditch",
    "price hike", "price rise", "raises prices", "raising prices", "hike", "shrinkflation",
    "hidden fees", "new fee", "crackdown", "u-turn", "reverses", "backtracks", "apologises",
    "apologizes", "apology", "scrapped", "scraps", "axed", "axes", "discontinued", "discontinues",
    "pulled from", "sold out", "outage", "down for", "chaos", "meltdown", "fans react", "people are",
    "internet", "tiktok", "trend", "trending", "banned", "bans", "ban on", "no longer", "slashes",
    "rip-off", "rip off", "ripoff", "scam", "not happy", "disappointed",
    # German
    "empörung", "ärger", "verärgert", "sauer", "wütend", "wut", "shitstorm", "kritik", "boykott",
    "preiserhöhung", "teurer", "abzocke", "gestrichen", "eingestellt", "aus für", "chaos", "ausfall",
    "störung", "proteste", "petition", "kündigen", "kündigungswelle", "netz tobt", "fans",
]

JARGON_WORDS = [
    "earnings", "quarterly", "revenue", "profit", "guidance", "analyst", "analysts", "shareholders",
    "investors", "merger", "acquisition", "acquires", "ipo", "bond", "dividend", "filing", "filed",
    "court", "judge", "lawsuit", "settlement", "ruling", "regulator", "sec", "ftc", "antitrust",
    "class action", "undeclared", "fda", "usda", "fsis", "voluntary recall", "recalls", "lot code",
    "best before", "contamination", "listeria", "salmonella", "e. coli",
    # German
    "quartal", "umsatz", "gewinn", "aktionäre", "übernahme", "gericht", "urteil", "klage",
    "rückruf", "lebensmittelwarnung",
]

SECTORS = {
    "streaming & subscriptions": ["netflix", "spotify", "disney+", "disney plus", "prime video", "youtube",
                                  "hbo", "streaming", "subscription", "subscribers", "paramount", "peacock",
                                  "dazn", "crunchyroll", "apple tv", "abo"],
    "airlines & travel":         ["airline", "flight", "flights", "passengers", "ryanair", "easyjet", "lufthansa",
                                  "british airways", "united airlines", "delta", "american airlines", "southwest",
                                  "wizz air", "eurowings", "condor", "airport", "baggage", "luggage", "hotel",
                                  "airbnb", "booking.com", "deutsche bahn", "train", "rail", "fahrgäste",
                                  "passagiere", "flug", "flüge", "bahn"],
    "food & drink":              ["mcdonald", "burger king", "kfc", "starbucks", "subway", "domino", "pizza hut",
                                  "taco bell", "chipotle", "wendy's", "greggs", "pret", "costa", "coca-cola",
                                  "coke", "pepsi", "red bull", "cadbury", "nestle", "nestlé", "haribo", "milka",
                                  "menu", "recipe", "restaurant", "diners", "flavour", "flavor", "chocolate",
                                  "snack", "drink", "coffee", "speisekarte", "rezept"],
    "supermarkets & retail":     ["walmart", "target", "costco", "aldi", "lidl", "tesco", "sainsbury", "asda",
                                  "morrisons", "rewe", "edeka", "kaufland", "dm", "ikea", "primark", "supermarket",
                                  "shoppers", "store", "stores", "self-checkout", "self checkout", "boots",
                                  "superdrug", "supermarkt", "filiale"],
    "tech & apps":               ["apple", "iphone", "samsung", "google", "gmail", "android", "instagram",
                                  "whatsapp", "facebook", "tiktok", "snapchat", "microsoft", "windows", "chatgpt",
                                  "openai", "app", "update", "playstation", "xbox", "nintendo", "steam", "duolingo",
                                  "tinder", "amazon", "ebay"],
    "telecom & internet":        ["vodafone", "telekom", "o2", "verizon", "at&t", "t-mobile", "sky", "broadband",
                                  "mobile network", "outage"],
    "banking & payments":        ["paypal", "klarna", "revolut", "n26", "bank", "banks", "card", "payments"],
    "delivery & ride-hailing":   ["uber", "lyft", "deliveroo", "uber eats", "lieferando", "doordash", "instacart",
                                  "dhl", "royal mail", "delivery", "lieferung", "parcel"],
    "fashion & beauty":          ["h&m", "zara", "nike", "adidas", "puma", "shein", "temu", "zalando", "gymshark",
                                  "sephora", "ulta", "lush", "fashion", "clothing", "beauty", "makeup"],
    "cars & mobility":           ["tesla", "volkswagen", "vw", "bmw", "car", "cars", "drivers", "ev", "charging"],
    "entertainment & tickets":   ["ticketmaster", "tickets", "concert", "tour", "festival", "lego", "barbie",
                                  "mattel", "game", "gaming"],
}

def _count(text, words):
    return sum(1 for w in words if ni._has_any(text, [w]))


def sector_of(text):
    """Best-matching sector (or 'other')."""
    best, best_n = "other", 0
    for sec, kws in SECTORS.items():
        n = _count(text, kws)
        if n > best_n:
            best, best_n = sec, n
    return best


def relatability(text, trending=False):
    """(qualifies?, details) — the relatable-story rule."""
    brand = ni._has_any(text, CONSUMER_BRANDS) or ni._has_any(text, A._KNOWN_LIST)
    everyday = _count(text, EVERYDAY_WORDS)
    reaction = _count(text, REACTION_WORDS)
    jargon = _count(text, JARGON_WORDS)
    anchor = brand or everyday >= 2
    if trending:
        ok = anchor and jargon <= 1 and (reaction >= 1 or brand)
    else:
        ok = anchor and reaction >= 1 and (jargon == 0 or (jargon == 1 and reaction >= 2))
    return ok, {"brand": brand, "everyday": everyday, "reaction": reaction, "jargon": jargon}


def _everyday_relevant(text, title):
    if ni._has_any(text, A.EXCLUDE_TERMS):
        return False
    if A.ROUNDUP_RX.search(title or ""):
        return False
    if ni._has_any(text, A.POLITICS_TERMS) and not (ni._has_any(text, CONSUMER_BRANDS)
                                                    or ni._has_any(text, A.STRONG_CORPORATE)):
        return False
    return relatability(text)[0]


def _everyday_kinds(text):
    kinds = A._brand_kinds(text)
    for k, kws in {
        "price":   ["price hike", "price rise", "raises prices", "raising prices", "hike", "new fee", "fees",
                    "hidden fees", "shrinkflation", "preiserhöhung", "teurer", "gebühr"],
        "change":  ["scrapped", "scraps", "axed", "axes", "discontinued", "discontinues", "no longer",
                    "recipe", "menu", "pulled from", "gestrichen", "eingestellt", "aus für"],
        "service": ["outage", "down for", "chaos", "meltdown", "delays", "cancelled", "ausfall", "störung"],
        "backlash": ["backlash", "boycott", "outrage", "viral", "fury", "furious", "shitstorm", "empörung"],
        "policy":  ["crackdown", "ban", "banned", "bans", "password", "rule", "rules"],
    }.items():
        if ni._has_any(text, kws):
            kinds.add(k)
    return kinds


def _everyday_categories(text):
    return {sector_of(text)}


def _everyday_severity(text):
    pts = 0
    for k, w in {"millions": 2, "million": 1, "nationwide": 1, "worldwide": 1, "every": 1, "all customers": 2,
                 "boycott": 2, "viral": 1, "outage": 1, "petition": 1, "thousands": 1, "u-turn": 1,
                 "apolog": 1, "price hike": 1, "bundesweit": 1, "weltweit": 1, "tausende": 1,
                 "millionen": 2}.items():
        if k in text:
            pts += w
    return min(1.0, pts / 5.0)


def _everyday_tokens(title):
    return ni._story_tokens(title, A.BRAND_GENERIC | set(w for w in REACTION_WORDS + EVERYDAY_WORDS if " " not in w))


def _gn(q, region):
    return (ni._gn(q, region), "EU" if region in ("DE", "AT", "IE") else region)


EVERYDAY_FEEDS = [
    _gn("customers+furious+OR+backlash+OR+outrage+brand", "US"),
    _gn("%22price+hike%22+OR+%22raises+prices%22+subscription+OR+streaming", "US"),
    _gn("fans+angry+OR+%22fans+furious%22+discontinued+OR+%22menu%22+OR+%22recipe%22", "US"),
    _gn("passengers+furious+OR+%22baggage+fees%22+OR+%22flights+cancelled%22+airline", "US"),
    _gn("shrinkflation+OR+%22hidden+fees%22+OR+tipping+customers", "US"),
    _gn("outage+users+OR+%22app+down%22+OR+%22not+working%22", "US"),
    _gn("boycott+brand+OR+%22goes+viral%22+customers", "US"),
    _gn("shoppers+furious+OR+customers+fuming+OR+%22price+rise%22", "UK"),
    _gn("passengers+OR+customers+%22slammed%22+OR+%22backlash%22", "UK"),
    _gn("Kunden+sauer+OR+Empörung+OR+Preiserhöhung", "DE"),
    _gn("Shitstorm+Marke+OR+Kunden+OR+Fans", "DE"),
    _gn("Abo+teurer+OR+Preiserh%C3%B6hung+Streaming+OR+Netflix", "DE"),
    ("https://www.theguardian.com/money/rss", "UK"),
    ("https://feeds.bbci.co.uk/news/business/rss.xml", "UK"),
    ("https://feeds.bbci.co.uk/news/technology/rss.xml", "UK"),
]

EVERYDAY_PROFILE = {
    "name": "everyday",
    "feeds": EVERYDAY_FEEDS,
    "relevant": _everyday_relevant,
    "tokens": _everyday_tokens,
    "kinds": _everyday_kinds,
    "categories": _everyday_categories,
    "severity": _everyday_severity,
}

# ─────────────────────────────────────────────
# Google Trends daily trending searches (free RSS) — what people actually search for today
# ─────────────────────────────────────────────

TRENDS_GEOS = [("US", "US"), ("GB", "UK"), ("DE", "EU")]
TRENDS_URLS = ["https://trends.google.com/trending/rss?geo={geo}",
               "https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"]


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _traffic(s):
    m = re.search(r"([\d,.]+)\s*([KkMm])?", s or "")
    if not m:
        return 0
    v = float(m.group(1).replace(",", ""))
    return int(v * (1000 if (m.group(2) or "").lower() == "k" else 1_000_000 if (m.group(2) or "").lower() == "m" else 1))


def pull_trending(seen=None):
    """News items attached to today's trending searches, as articles (same shape as RSS articles).
    Returns (articles, stats). Each article carries 'trend' (search term) and 'traffic'."""
    seen = seen if seen is not None else set()
    out, ok, total = [], 0, 0
    now = time.time()
    for geo, region in TRENDS_GEOS:
        xml = None
        for u in TRENDS_URLS:
            try:
                r = requests.get(u.format(geo=geo), timeout=ni.FEED_TIMEOUT, headers=ni.UA)
                if r.status_code == 200 and b"<item" in r.content:
                    xml = r.content
                    break
            except Exception:
                continue
        if not xml:
            print(f"⚠️ Trends {geo} unavailable")
            continue
        ok += 1
        try:
            root = ET.fromstring(xml)
        except Exception as e:
            print(f"⚠️ Trends {geo} parse error: {e}")
            continue
        for item in root.iter():
            if _local(item.tag) != "item":
                continue
            term, traffic, news = "", 0, []
            for ch in item:
                t = _local(ch.tag)
                if t == "title":
                    term = (ch.text or "").strip()
                elif t == "approx_traffic":
                    traffic = _traffic(ch.text)
                elif t == "news_item":
                    d = {_local(x.tag): (x.text or "").strip() for x in ch}
                    news.append(d)
            for d in news:
                title = ni._clean_title(d.get("news_item_title", ""), d.get("news_item_source", ""))
                src = d.get("news_item_source") or "Unknown"
                if not title:
                    continue
                total += 1
                key = (title.lower(), src.lower())
                if key in seen:
                    continue
                seen.add(key)
                text = (title + " " + term).lower()
                if ni._has_any(text, A.EXCLUDE_TERMS) or A.ROUNDUP_RX.search(title):
                    continue
                if ni._has_any(text, A.POLITICS_TERMS) and not ni._has_any(text, CONSUMER_BRANDS):
                    continue
                if not relatability(text, trending=True)[0]:
                    continue
                url = d.get("news_item_url", "")
                out.append({
                    "title": title, "source": src, "url": url, "ts": now,
                    "region": ni._outlet_region(url, region),
                    "tokens": _everyday_tokens(title), "hazards": _everyday_kinds(text),
                    "categories": _everyday_categories(text), "severity": _everyday_severity(text),
                    "negativity": ni._negativity(text), "spread": ni._has_any(text, ni.SPREAD_TERMS),
                    "raw_title": title, "trend": term, "traffic": traffic,
                })
    print(f"📈 [everyday] trends {ok}/{len(TRENDS_GEOS)} OK | {len(out)} relatable / {total} trending news items")
    return out, {"trends_ok": ok}
