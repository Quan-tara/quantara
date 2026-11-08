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
    "vinted", "etsy", "depop", "jetblue", "trainline", "skims", "duckduckgo", "rogers", "nvidia", "meta",
    "disney", "sony", "hulu", "roku", "twitch", "discord", "reddit", "pinterest", "linkedin", "zoom",
    "canva", "adobe", "dropbox", "robinhood", "coinbase", "venmo", "cash app", "chase", "wells fargo",
    "bank of america", "barclays", "hsbc", "lloyds", "natwest", "monzo", "starling", "virgin media",
    "bt", "ee", "comcast", "xfinity", "spectrum", "spirit airlines", "frontier airlines", "alaska airlines",
    "jet2", "tui", "virgin atlantic", "emirates", "qatar airways", "air canada", "westjet", "expedia",
    "hertz", "avis", "waitrose", "marks & spencer", "m&s", "john lewis", "argos", "currys", "home depot",
    "lowe's", "best buy", "kroger", "trader joe's", "whole foods", "safeway", "cvs", "walgreens",
    "dunkin", "krispy kreme", "tim hortons", "nando's", "wetherspoons", "five guys", "chick-fil-a",
    "popeyes", "papa john's", "shake shack", "panera", "heinz", "kellogg's", "mars", "hershey", "oreo",
    "doritos", "pringles", "monster energy", "prime hydration", "stanley", "crocs", "lululemon", "under armour",
    "new balance", "converse", "vans", "uniqlo", "gap", "old navy", "victoria's secret", "asos", "boohoo",
    "ugg", "airpods", "ipad", "macbook", "galaxy", "pixel", "kindle", "alexa", "fortnite", "epic games",
    "roblox", "minecraft", "call of duty", "ubisoft", "valve", "rockstar", "gta", "ea sports", "bud light",
    "anheuser-busch", "jaguar", "harley-davidson", "ford", "toyota", "honda",
]

EVERYDAY_WORDS = [
    "subscription", "subscriptions", "subscribers", "customers", "shoppers", "passengers", "travellers",
    "travelers", "diners", "fans", "users", "parents", "drivers", "commuters", "menu", "recipe",
    "flavour", "flavor", "app", "update", "price", "prices", "fee", "fees", "charge", "charges",
    "ticket", "tickets", "flight", "flights", "store", "stores", "supermarket", "restaurant",
    "delivery", "account", "password", "ads", "advert", "streaming", "phone", "loyalty", "points",
    "refund", "queue", "self-checkout", "self checkout", "tipping", "tip", "baggage", "luggage",
    "drink", "drinks", "outage", "network", "holiday", "console", "game", "games", "seat", "seats",
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
    # executive / investor talk — not what ordinary people argue about
    "cfo", "chief financial officer", "valuation", "funding round", "investor", "strategy",
    "interview", "keynote", "earnings call", "roadmap", "fiscal", "ipo",
]

# ─────────────────────────────────────────────
# FOOD & DRINK focus — the game lists only food stories (same industry as the FSNI)
# ─────────────────────────────────────────────
FOOD_BRANDS = [
    "mcdonald's", "mcdonalds", "burger king", "kfc", "starbucks", "subway", "domino's", "dominos",
    "pizza hut", "taco bell", "chipotle", "wendy's", "greggs", "pret", "costa", "coca-cola", "coke",
    "pepsi", "red bull", "cadbury", "nestle", "nestlé", "haribo", "milka", "dunkin", "krispy kreme",
    "tim hortons", "nando's", "wetherspoons", "five guys", "chick-fil-a", "popeyes", "papa john's",
    "shake shack", "panera", "heinz", "kellogg's", "kellanova", "mars", "hershey", "oreo", "doritos",
    "pringles", "monster energy", "prime hydration", "bud light", "anheuser-busch", "deliveroo",
    "uber eats", "doordash", "instacart", "just eat", "whole foods", "trader joe's", "kraft", "mondelez",
    "danone", "unilever", "ben & jerry's", "walkers", "lay's", "cheetos", "skittles", "m&m's", "twix",
    "snickers", "kitkat", "nutella", "ferrero", "lindt", "guinness", "heineken", "budweiser", "corona",
    "stella artois", "innocent", "oatly", "beyond meat", "impossible foods", "hellofresh", "gail's",
    "itsu", "wagamama", "leon", "pizza express", "frankie & benny's", "harvester", "toby carvery",
    "olive garden", "applebee's", "ihop", "denny's", "sonic", "arby's", "jack in the box", "white castle",
    "in-n-out", "raising cane's", "dairy queen", "baskin-robbins", "cinnabon", "auntie anne's",
]
GROCERS = ["walmart", "target", "costco", "aldi", "lidl", "tesco", "sainsbury's", "asda", "morrisons",
           "waitrose", "m&s", "marks & spencer", "kroger", "safeway", "whole foods", "trader joe's",
           "co-op", "iceland", "ocado", "publix", "h-e-b", "wegmans", "albertsons"]
FOOD_WORDS = [
    "food", "foods", "menu", "recipe", "restaurant", "restaurants", "fast food", "fast-food", "takeaway",
    "takeout", "diners", "chef", "snack", "snacks", "drink", "drinks", "soda", "beer", "coffee", "tea",
    "chocolate", "candy", "sweets", "crisps", "chips", "burger", "burgers", "pizza", "fries", "sandwich",
    "sandwiches", "chicken nuggets", "nuggets", "cereal", "breakfast", "lunch", "dinner", "meal",
    "meals", "groceries", "grocery", "food prices", "flavour", "flavor", "ingredient", "ingredients",
    "vegan", "plant-based", "ultra-processed", "seed oils", "sugar", "calories", "protein", "milk",
    "cheese", "eggs", "bread", "butter", "meat", "steak", "dessert", "ice cream", "doughnut", "donut",
    "latte", "frappuccino", "milkshake", "happy meal", "school lunch", "school meals", "drive-thru",
    "drive-through", "portion", "portions", "tipping", "brunch", "bakery", "pub", "bar",
]
# words that make a food-safety story a debate, not a notice
DEBATE_WORDS = [
    "blame", "blames", "blamed", "denies", "denied", "deny", "defends", "defended", "responds", "slammed",
    "furious", "fury", "outrage", "backlash", "boycott", "cover-up", "covered up", "knew", "families sue",
    "sues", "sued", "petition", "apologises", "apologizes", "apology", "under fire", "questions over",
    "calls for", "demand answers", "anger", "angry", "devastated", "speaks out", "accused", "accuses",
    "fight back", "fighting back", "destroyed", "unfair", "scapegoat", "reopens", "reopening",
]
RECALL_NOTICE_RX = re.compile(
    r"recalls?\b.*\b(due to|because of|over potential|for possible|over possible)|\bfda\b|\busda\b|\bfsis\b|"
    r"voluntary recall|lot code|best before|use by|\bundeclared\b|\bdo not eat\b", re.I)

SECTORS = {
    "fast food & restaurants": ["mcdonald", "burger king", "kfc", "starbucks", "subway", "domino", "pizza hut",
                                "taco bell", "chipotle", "wendy's", "greggs", "pret", "costa", "dunkin", "nando",
                                "wetherspoons", "five guys", "chick-fil-a", "popeyes", "papa john", "shake shack",
                                "panera", "wagamama", "olive garden", "restaurant", "restaurants", "fast food",
                                "fast-food", "menu", "diners", "drive-thru", "drive-through", "takeaway", "takeout",
                                "chef", "tipping", "deliveroo", "uber eats", "doordash", "just eat", "happy meal"],
    "snacks & drinks":         ["coca-cola", "coke", "pepsi", "red bull", "cadbury", "haribo", "milka", "mars",
                                "hershey", "oreo", "doritos", "pringles", "monster energy", "prime hydration",
                                "bud light", "beer", "soda", "snack", "snacks", "drink", "drinks", "chocolate",
                                "candy", "sweets", "crisps", "chips", "walkers", "kitkat", "nutella", "coffee",
                                "latte", "energy drink", "oatly", "innocent"],
    "groceries & prices":      ["supermarket", "supermarkets", "grocery", "groceries", "food prices", "shoppers",
                                "shrinkflation", "walmart", "costco", "aldi", "lidl", "tesco", "sainsbury", "asda",
                                "morrisons", "waitrose", "kroger", "whole foods", "trader joe's", "price hike",
                                "price rise", "prices"],
    "food safety":             ["outbreak", "recall", "listeria", "salmonella", "e. coli", "e.coli", "contaminat",
                                "food poisoning", "sickened", "hospitalised", "hospitalized", "norovirus"],
    "diets & food trends":     ["vegan", "plant-based", "ultra-processed", "seed oils", "sugar", "calories",
                                "protein", "diet", "trend", "tiktok", "viral", "recipe", "influencer", "keto",
                                "ozempic", "school lunch", "school meals"],
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


_SAFETY_JARGON = {"recalls", "contamination", "listeria", "salmonella", "e. coli", "undeclared", "fda",
                  "usda", "fsis", "voluntary recall", "lot code", "best before"}
_JARGON_GENERAL = [w for w in JARGON_WORDS if w not in _SAFETY_JARGON]


def food_anchor(text):
    """Is the story about food & drink?"""
    if ni._has_any(text, FOOD_BRANDS):
        return True
    food = _count(text, FOOD_WORDS)
    if ni._has_any(text, GROCERS):
        return food >= 1                        # a supermarket story only when it's about food
    return food >= 1


def relatability(text, trending=False, title=""):
    """(qualifies?, details) — the relatable food-story rule.
    • about food & drink, with a human reaction, little business/legal jargon
    • food-safety stories only when there is a debate (blame, denial, outrage…), never a recall notice"""
    anchor = food_anchor(text)
    reaction = _count(text, REACTION_WORDS)
    jargon = _count(text, _JARGON_GENERAL)
    safety = ni._has_any(text, ni.HAZARD_TERMS)
    if safety:                                  # a food-safety hazard is a food story by itself
        debate = _count(text, DEBATE_WORDS)
        ok = debate >= 1 and not RECALL_NOTICE_RX.search(title or text) and jargon <= 1
    else:
        ok = anchor and reaction >= 1 and (jargon == 0 or (jargon == 1 and reaction >= 2))
    return ok, {"food": anchor, "reaction": reaction, "jargon": jargon, "safety": safety}


# shopping deals and coupon posts are not controversies
DEAL_RX = re.compile(r"\$\d[\d,]*\s*-?\s*off|\d+\s*%\s*off|\bdeals?\b|\bdiscount|\bon sale\b|"
                     r"\bcoupon|\bpromo code|\bblack friday\b|\bcyber monday\b|\bprime day\b", re.I)
# German (or other non-English) headlines are left out for now
_DE_RX = re.compile(r"\b(der|die|das|und|für|über|nicht|mit|wegen|teurer|erwägt|keine)\b|[äöüß]", re.I)


# Indian-market stories (rupees, lakh/crore, Indian places) — not our US/UK audience for now
_IN_RX = re.compile(r"₹|\brs\.?\s?\d|\binr\b|\blakh\b|\bcrore\b|\bindia(n)?\b|\bdiwali\b|\bkarnataka\b|"
                    r"\bmumbai\b|\bdelhi\b|\bbengaluru\b|\bbangalore\b|\bchennai\b|\bhyderabad\b|"
                    r"\bkolkata\b|\bpune\b|\bkerala\b|\btamil nadu\b|\bmaharashtra\b", re.I)


# list articles ("5 Finds Costco Already Discontinued", "10 best snacks", "Every pizza chain, ranked")
# — nobody argues about them and nobody changes what they buy over them
LISTICLE_RX = re.compile(
    r"^\W*\d{1,2}\s+(?:[\w'’-]+\s+){0,3}?(?:things|finds|ways|reasons|foods|items|products|snacks|drinks|"
    r"recipes|brands|restaurants|chains|spots|places|tips|tricks|hacks|picks|favorites|favourites|"
    r"best|worst|must|essentials|staples|meals|dishes|treats|sweets|desserts|cereals|chips|candies|"
    r"sodas|beers|wines|grocery|groceries|costco|trader|aldi|walmart|target|kroger)\b"
    r"|\b(?:the\s+)?(?:top\s+)?\d{1,2}\s+best\b|\branked\b|\branking\b|\btaste[- ]test\b|"
    r"\bwe tried\b|\bi tried\b|\bbest\s+[\w\s]{0,20}\bto\s+(?:buy|try)\b|\bshopping list\b", re.I)


def blocked_title(text, title):
    """Shared hard filters: deals, list articles, politics, religion/ethnicity/war."""
    t = title or ""
    if DEAL_RX.search(t) or LISTICLE_RX.search(t):
        return True
    return ni._has_any(text, A.HARD_POLITICS) or ni._has_any(text, A.SENSITIVE_TERMS)


def english_only(title):
    """English, and meant for a US/UK/European audience."""
    t = title or ""
    return not _DE_RX.search(t) and not _IN_RX.search(t)


def _everyday_relevant(text, title):
    if not english_only(title) or blocked_title(text, title):
        return False
    if ni._has_any(text, A.EXCLUDE_TERMS):
        return False
    if A.ROUNDUP_RX.search(title or ""):
        return False
    if ni._has_any(text, A.POLITICS_TERMS) and not (ni._has_any(text, FOOD_BRANDS)
                                                    or ni._has_any(text, A.STRONG_CORPORATE)):
        return False
    return relatability(text, title=title)[0]


def _everyday_kinds(text):
    kinds = A._brand_kinds(text)
    for k, kws in {
        "price":   ["price hike", "price rise", "raises prices", "raising prices", "hike", "new fee", "fees",
                    "hidden fees", "shrinkflation", "preiserhöhung", "teurer", "gebühr"],
        "change":  ["scrapped", "scraps", "axed", "axes", "discontinued", "discontinues", "no longer",
                    "recipe", "menu", "pulled from", "gestrichen", "eingestellt", "aus für"],
        "service": ["outage", "down for", "chaos", "meltdown", "delays", "cancelled", "crash", "crashes",
                    "crashed", "not working", "is down", "went down", "ausfall", "störung"],
        "backlash": ["backlash", "boycott", "outrage", "viral", "fury", "furious", "shitstorm", "empörung"],
        "policy":  ["crackdown", "ban", "banned", "bans", "password", "rule", "rules"],
    }.items():
        if ni._has_any(text, kws):
            kinds.add(k)
    return kinds


def _everyday_categories(text):
    return {sector_of(text)}


def _everyday_severity(text):
    harm = ni._severity(text) if ni._has_any(text, ni.HAZARD_TERMS) else 0.0
    pts = 0
    for k, w in {"millions": 2, "million": 1, "nationwide": 1, "worldwide": 1, "every": 1, "all customers": 2,
                 "boycott": 2, "viral": 1, "outage": 1, "petition": 1, "thousands": 1, "u-turn": 1,
                 "apolog": 1, "price hike": 1, "bundesweit": 1, "weltweit": 1, "tausende": 1,
                 "millionen": 2}.items():
        if k in text:
            pts += w
    return max(harm, min(1.0, pts / 5.0))


def _everyday_tokens(title):
    return ni._story_tokens(title, A.BRAND_GENERIC | set(w for w in REACTION_WORDS + EVERYDAY_WORDS if " " not in w))


def _gn(q, region):
    return (ni._gn(q, region), "EU" if region in ("DE", "AT", "IE") else region)


EVERYDAY_FEEDS = [
    _gn("fast+food+customers+furious+OR+backlash+OR+outrage", "US"),
    _gn("%22menu+change%22+OR+%22recipe+change%22+OR+discontinued+fans+food+OR+drink", "US"),
    _gn("restaurant+chain+backlash+OR+boycott+OR+%22customers+slam%22", "US"),
    _gn("snack+OR+chocolate+OR+soda+discontinued+fans+OR+petition", "US"),
    _gn("grocery+prices+shoppers+furious+OR+shrinkflation+food", "US"),
    _gn("viral+food+OR+tiktok+food+backlash+OR+divided", "US"),
    _gn("%22ultra-processed%22+OR+%22seed+oils%22+OR+%22school+lunch%22+backlash+OR+debate", "US"),
    _gn("food+outbreak+blame+OR+denies+OR+%22families+sue%22+OR+reopens", "US"),
    _gn("supermarket+food+prices+shoppers+fuming+OR+furious", "UK"),
    _gn("fast+food+OR+restaurant+customers+slammed+OR+backlash", "UK"),
    ("https://www.theguardian.com/food/rss", "UK"),
    ("https://www.eater.com/rss/index.xml", "US"),
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

TRENDS_GEOS = [("US", "US"), ("GB", "UK")]          # English only for now (German later)
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
                if not english_only(title) or blocked_title(text, title):
                    continue
                if ni._has_any(text, A.POLITICS_TERMS) and not ni._has_any(text, FOOD_BRANDS):
                    continue
                if not relatability(text, trending=True, title=title)[0]:
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


def brand_in(titles):
    """The consumer brand the headlines are about: most mentioned, earliest first.
    Returned in the spelling the headline uses ("McDonald's", "PlayStation")."""
    counts, first, shown = {}, {}, {}
    all_brands = list(dict.fromkeys(FOOD_BRANDS + GROCERS + CONSUMER_BRANDS))
    for i, t in enumerate(titles or []):
        low = t.lower()
        for b in all_brands:
            m = re.search(r"(?<![\w])" + re.escape(b) + r"(?![\w])", low)
            if not m:
                continue
            counts[b] = counts.get(b, 0) + 1
            first.setdefault(b, i * 1000 + m.start())
            shown.setdefault(b, t[m.start():m.end()])
    if not counts:
        return None
    # a longer brand that contains a shorter one wins ("playstation plus" over "plus", "bud light" over "light")
    best = sorted(counts, key=lambda b: (-counts[b], first[b], -len(b)))[0]
    for b in counts:
        if b != best and best in b and counts[b] >= counts[best] - 0:
            best = b
    return shown[best]


# ─────────────────────────────────────────────
# REACH CHECK — how widely is a candidate really covered?
# Our feeds usually catch one or two articles per everyday story. For the strongest
# candidates each run we do one extra Google News search ("<brand> <topic words>",
# last 3 days) and add the articles that clearly are the same story: same brand in the
# headline + at least one of the topic words. Then the story is regrouped and scored.
# ─────────────────────────────────────────────
import urllib.parse as _up
import feedparser as _fp

REACH_TOP       = 8          # candidates checked per run
REACH_CACHE_MIN = 60         # same search not repeated within this time
_reach_cache = {}            # query → (ts, items)

_TOPIC_SKIP = set("""
fans customers users people shoppers passengers subscribers parents buyers owners viewers players
says said new now today after amid over again still just more first last this that what why how
when where with from into about their there they them your some many much very really could would
should will been being have has had were was are its it's year years week weeks month months day days
""".split())
# subject words (what the story is about) rank first; reaction words ("furious") say nothing about the subject
_TOPIC_SUBJECT = {w for w in EVERYDAY_WORDS + FOOD_WORDS if " " not in w and len(w) >= 3} - _TOPIC_SKIP - {"food", "foods"}
_TOPIC_REACTION = {w for w in REACTION_WORDS if " " not in w}
_TOPIC_PRIORITY = _TOPIC_SUBJECT


def topic_words(titles, company, n=2):
    """Words that say what the story is about (besides the brand), e.g. 'baggage fees'."""
    comp = set(re.findall(r"[a-z0-9]+", (company or "").lower()))
    counts, first = {}, {}
    for i, t in enumerate(titles or []):
        for j, w in enumerate(re.findall(r"[a-z][a-z0-9'-]+", t.lower())):
            w = w.strip("'-")
            if w in comp or w in _TOPIC_SKIP or (len(w) < 4 and w not in _TOPIC_SUBJECT):
                continue
            if w not in _TOPIC_SUBJECT and (w in A.COMMON_WORDS or w in ni.GENERIC):
                continue
            counts[w] = counts.get(w, 0) + 1
            first.setdefault(w, i * 100 + j)
    if not counts:
        return []
    rank = lambda w: 2 if w in _TOPIC_SUBJECT else (0 if w in _TOPIC_REACTION else 1)
    ranked = sorted(counts, key=lambda w: (-rank(w), -counts[w], first[w]))
    return ranked[:n]


def _search(query):
    now = time.time()
    hit = _reach_cache.get(query)
    if hit and now - hit[0] < REACH_CACHE_MIN * 60:
        return hit[1]
    url = ni._gn(_up.quote_plus(query), "US")
    items = []
    try:
        r = requests.get(url, timeout=ni.FEED_TIMEOUT, headers=ni.UA)
        if r.status_code == 200:
            for e in _fp.parse(r.content).entries[:ni.MAX_PER_FEED]:
                src, href = "", ""
                if e.get("source") and hasattr(e.source, "get"):
                    src = (e.source.get("title") or "").strip()
                    href = e.source.get("href") or e.source.get("url") or ""
                src = src or "Unknown"
                title = ni._clean_title(e.get("title", ""), src)
                if title:
                    items.append({"title": title, "source": src, "href": href,
                                  "url": e.get("link", ""), "ts": ni._entry_ts(e) or now})
    except Exception as ex:
        print(f"⚠️ reach search failed ({query}): {ex}")
    _reach_cache[query] = (now, items)
    if len(_reach_cache) > 500:
        for k in sorted(_reach_cache, key=lambda k: _reach_cache[k][0])[:250]:
            _reach_cache.pop(k, None)
    return items


def reach_check(stories, articles, top=REACH_TOP):
    """Extra articles (same shape as feed articles) for the strongest candidates."""
    seen = {(a["title"].lower(), a["source"].lower()) for a in articles}
    cutoff = time.time() - ni.ARTICLE_WINDOW_H * 3600
    cands = []
    for s in stories:
        brand = brand_in(s.get("raw_titles"))
        if brand:
            cands.append((ni.story_score(s), s, brand))
    cands.sort(key=lambda x: x[0], reverse=True)
    extra, searched = [], 0
    for _, s, brand in cands[:top]:
        words = topic_words(s.get("raw_titles"), brand)
        if not words:
            continue
        query = f'"{brand}" ' + " ".join(words)          # the search feed itself limits to recent days
        searched += 1
        b_low = brand.lower()
        for it in _search(query):
            t, tl = it["title"], it["title"].lower()
            key = (tl, it["source"].lower())
            if key in seen or it["ts"] < cutoff:
                continue
            if b_low not in tl or not any(w in tl for w in words):
                continue                                # not clearly the same story
            if not english_only(t) or blocked_title(tl, t) or ni._has_any(tl, A.EXCLUDE_TERMS):
                continue
            seen.add(key)
            extra.append({
                "title": t, "source": it["source"], "url": it["url"], "ts": it["ts"],
                "region": ni._outlet_region(it["href"], "US"),
                "tokens": _everyday_tokens(t), "hazards": _everyday_kinds(tl),
                "categories": _everyday_categories(tl), "severity": _everyday_severity(tl),
                "negativity": ni._negativity(tl), "spread": ni._has_any(tl, ni.SPREAD_TERMS),
                "raw_title": t, "reach": True,
            })
    print(f"🔭 [everyday] reach check: {searched} searches → {len(extra)} more articles")
    return extra
