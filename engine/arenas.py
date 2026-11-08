"""
Arenas — story collectors beyond food
=====================================
Same engine as the food collector (grouping, story score, stable ids, listing, trading,
comments). Each arena only brings its own feeds and word lists.

  food    — engine/narrative_index.py (feeds the FSNI; unchanged)
  brands  — corporate and brand scandals: lawsuits, regulators, fraud, data breaches,
            boycotts, leadership crises, layoffs, product failures. English + German.

Brand arena rules
  • relevant  = a scandal word AND a company word (so politics/sports/crime don't qualify)
  • excluded  = violent crime, private-life gossip, deaths of individuals; and food-safety
                articles (those belong to the food arena)
  • companies and public roles only — stories about private people are filtered out

Nothing here touches the FSNI. Brand readings are stored in arena_readings.
"""

import json
import re
import time
from collections import Counter

from engine import narrative_index as ni

# ─────────────────────────────────────────────
# BRAND SCANDALS — vocabulary
# ─────────────────────────────────────────────

SCANDAL_TERMS = [
    "scandal", "lawsuit", "sued", "sues", "class action", "boycott", "backlash", "outrage",
    "fraud", "probe", "investigation", "investigated", "fined", "penalty", "settlement",
    "data breach", "breach", "hackers", "hacked", "hack", "cyberattack", "leak", "leaked", "recall", "resigns",
    "resignation", "ousted", "steps down", "layoffs", "lays off", "job cuts", "strike",
    "walkout", "bankruptcy", "insolvency", "accused", "allegations", "alleged", "misconduct",
    "cover-up", "whistleblower", "antitrust", "banned", "controversy", "apologizes",
    "apologises", "apology", "crisis", "outage", "shares plunge", "shares tumble",
    "stock plunges", "under fire", "faces criticism",
    # German
    "skandal", "klage", "verklagt", "sammelklage", "boykott", "shitstorm", "empörung",
    "betrug", "ermittlungen", "ermittelt", "bußgeld", "strafe", "datenleck", "hackerangriff",
    "cyberangriff", "rückruf", "rücktritt", "tritt zurück", "stellenabbau", "entlassungen",
    "streik", "insolvenz", "pleite", "vorwürfe", "vorwurf", "krise", "panne", "ausfall",
    "kartellamt", "razzia",
]

CORPORATE_TERMS = [
    "company", "companies", "firm", "brand", "corporation", "corp", "inc", "ltd", "plc",
    "maker", "manufacturer", "retailer", "airline", "bank", "carmaker", "automaker", "chain",
    "startup", "giant", "ceo", "chief executive", "executive", "shareholders", "shares",
    "stock", "investors", "customers", "users", "regulator", "regulators", "sec", "ftc",
    "fca", "ofcom", "european commission", "watchdog", "employees", "workers", "staff",
    # German
    "unternehmen", "konzern", "firma", "marke", "hersteller", "händler", "kette",
    "fluggesellschaft", "autobauer", "vorstand", "konzernchef", "aktie", "aktionäre",
    "anleger", "kunden", "nutzer", "behörde", "bafin", "mitarbeiter", "beschäftigte",
    "ag", "gmbh",
]

EXCLUDE_TERMS = [
    # violent crime / personal tragedy / private life — not what this arena is about
    "murder", "murdered", "rape", "raped", "stabbing", "stabbed", "shooting", "shot dead",
    "homicide", "sexual assault", "child abuse", "molest", "kidnapping", "kidnapped",
    "suicide", "overdose", "divorce", "dating", "girlfriend", "boyfriend", "wedding",
    "pregnant", "affair", "custody battle", "funeral", "obituary", "dies at",
    # German
    "mord", "ermordet", "vergewaltigung", "messerangriff", "missbrauch", "entführung",
    "suizid", "scheidung", "hochzeit", "schwanger", "affäre", "beerdigung", "nachruf",
]

# Roundups pack several stories into one headline ("X resigns; Y loses €100mn …") — they
# would glue unrelated stories together, so they are left out.
ROUNDUP_RX = re.compile(
    r";|\bwrap[- ]?up\b|\bround[- ]?up\b|\bbriefing\b|what we know|\bdigest\b|live updates|"
    r"\blive:|top stories|the latest:|week in review|this week in|\brecap\b|newsletter|"
    r"morning report|evening report|news in brief|kurz gemeldet|nachrichten des tages|"
    r"im überblick|\bticker\b|\bnewsblog\b", re.I)

# Politics: left out unless a company is clearly the subject
POLITICS_TERMS = [
    "president", "prime minister", "minister", "chancellor", "parliament", "senate", "senator",
    "congress", "congressman", "lawmakers", "election", "elections", "campaign", "party leader",
    "government", "mayor", "governor", "opposition", "coalition", "cabinet", "white house",
    "kremlin", "downing street", "impeach", "referendum",
    "midterm", "midterms", "voter", "voters", "vote", "votes", "voting", "ballot", "polls",
    "democrat", "democrats", "republican", "republicans", "gop", "trump", "biden", "harris",
    "vance", "starmer", "tories", "labour party", "reform uk", "merz", "scholz",
    "bundestag", "bundesregierung", "kanzler", "ministerin", "regierung", "partei", "wahlkampf",
    "koalition", "bürgermeister", "landtag", "wahl", "wähler", "umfrage", "afd", "cdu", "spd",
]
# clearly political: no brand can make these a consumer story
HARD_POLITICS = [
    "prime minister", "president", "chancellor", "minister", "government", "midterm", "midterms", "voter", "voters", "election", "elections", "ballot", "campaign",
    "democrats", "republicans", "gop", "congress", "senate", "parliament", "lawmakers",
    "wahlkampf", "wahl", "wähler", "bundestag", "landtag",
]
# religion, ethnicity, war and the Middle East — too heated for a betting game, never listed
SENSITIVE_TERMS = [
    "jewish", "jew", "jews", "muslim", "islam", "islamic", "christian", "catholic", "hindu", "sikh",
    "religious", "religion", "synagogue", "mosque", "rabbi", "imam", "antisemitic",
    "antisemitism", "islamophobia", "islamophobic", "israel", "israeli", "gaza", "palestine",
    "palestinian", "hamas", "hezbollah", "zionist", "west bank", "racist", "racism", "racial",
    "ethnic", "ethnicity", "genocide", "terrorist", "terrorism", "shooting", "airstrike", "ceasefire",
]
STRONG_CORPORATE = [
    "inc", "corp", "corporation", "plc", "ltd", "llc", "ag", "gmbh", "company", "companies", "firm",
    "ceo", "chief executive", "shares", "shareholders", "stock", "brand", "retailer", "manufacturer",
    "unternehmen", "konzern", "firma", "konzernchef", "aktie", "aktionäre", "marke", "hersteller",
]

# Never a company name: countries, big cities/regions, road and route codes
PLACES = set("""
afghanistan albania algeria argentina armenia australia austria azerbaijan bahrain bangladesh belarus
belgium bolivia bosnia brazil bulgaria cambodia cameroon canada chile china colombia congo croatia cuba
cyprus czechia denmark ecuador egypt england estonia ethiopia finland france georgia germany ghana greece
guatemala haiti honduras hungary iceland india indonesia iran iraq ireland israel italy jamaica japan
jordan kazakhstan kenya korea kosovo kuwait latvia lebanon libya lithuania luxembourg malaysia mali malta
mexico moldova mongolia montenegro morocco myanmar nepal netherlands nicaragua niger nigeria norway oman
pakistan panama paraguay peru philippines poland portugal qatar romania russia rwanda scotland senegal
serbia singapore slovakia slovenia somalia spain sudan sweden switzerland syria taiwan tanzania thailand
tunisia turkey uganda ukraine uruguay uzbekistan venezuela vietnam wales yemen zambia zimbabwe
london paris berlin munich münchen hamburg frankfurt cologne köln vienna wien zurich madrid rome milan
brussels amsterdam dublin lisbon warsaw prague budapest moscow kyiv istanbul dubai beijing shanghai
tokyo seoul delhi mumbai sydney melbourne toronto vancouver montreal chicago boston seattle houston
dallas atlanta miami denver phoenix detroit california texas florida york washington ohio michigan
illinois georgia virginia carolina arizona nevada oregon colorado massachusetts pennsylvania jersey
bavaria bayern hessen sachsen nrw manchester birmingham liverpool leeds glasgow edinburgh bristol
redditch silicon valley wall street europe asia africa americas
philadelphia brooklyn manhattan queens bronx harlem baltimore pittsburgh cleveland cincinnati columbus
indianapolis milwaukee minneapolis nashville memphis louisville charlotte raleigh richmond orlando tampa
jacksonville austin antonio sacramento oakland portland diego francisco angeles vegas orleans honolulu
anchorage omaha tulsa albuquerque tucson newark buffalo hartford providence st louis kansas missouri
wisconsin minnesota tennessee kentucky alabama louisiana mississippi arkansas oklahoma iowa nebraska
utah idaho montana wyoming dakota hawaii alaska maine vermont connecticut maryland delaware
cardiff swansea newport belfast aberdeen dundee sheffield nottingham leicester coventry newcastle
brighton southampton portsmouth cambridge oxford york norwich exeter plymouth cornwall devon kent essex
yorkshire lancashire surrey sussex dorset somerset cumbria
""".split())
_ROAD_RX = re.compile(r"^(?:[a-z]{1,2}-?\d{1,4}|\d+)$", re.I)     # M56, A9, I-95, B12, 2026
# everyday headline words that are never (part of the edge of) a company name
COMMON_WORDS = set("""
is are was were be been am do does did has have had will would can could should may might must
here there this that these those what why how when where which you your we our they their it its
all any every more most less new top best worst big biggest small after before over under still
just now today tonight right left down up out off back again also only even very really much
gets get got makes make made says said report reports reported users user customers customer
app apps website websites site outage outages update updates live news video watch price prices
fans people everyone nobody adds adding launches launch announces unveils reveals confirms
including includes plus one two three four five six seven eight nine ten first last next week
month year day days hours minutes free deal deals sale shop shopping online service services
""".split())
_LEAD_STRIP = COMMON_WORDS | {"the", "a", "an", "and", "of", "for", "to", "in", "on", "at", "by", "with", "from", "as",
               "breaking", "exclusive", "watch", "video", "live", "update", "opinion", "analysis",
               "report", "new", "latest", "how", "why", "what", "when", "where", "after", "amid", "over"}

BRAND_SEVERITY = {
    "fraud": 3, "criminal": 2, "charged": 2, "indicted": 3, "arrested": 2, "raid": 2,
    "fined": 2, "billion": 1, "million": 1, "class action": 2, "lawsuit": 1, "probe": 1,
    "investigation": 1, "resigns": 2, "ousted": 2, "steps down": 2, "boycott": 2,
    "data breach": 2, "hacked": 1, "layoffs": 1, "job cuts": 1, "bankruptcy": 3,
    "insolvency": 3, "shares plunge": 2, "shares tumble": 2, "stock plunges": 2,
    "recall": 1, "deaths": 3, "killed": 3, "injured": 2, "nationwide": 1, "worldwide": 1,
    "global": 1, "whistleblower": 1, "cover-up": 2,
    # German
    "betrug": 3, "anklage": 2, "razzia": 2, "festgenommen": 2, "bußgeld": 2, "milliarden": 1,
    "sammelklage": 2, "klage": 1, "ermittlungen": 1, "rücktritt": 2, "boykott": 2,
    "datenleck": 2, "stellenabbau": 1, "entlassungen": 1, "insolvenz": 3, "pleite": 3,
    "rückruf": 1, "tote": 3, "verletzte": 2, "bundesweit": 1, "weltweit": 1,
}

BRAND_KINDS = {
    "legal":      ["lawsuit", "sued", "sues", "class action", "court", "klage", "verklagt", "sammelklage", "gericht"],
    "regulator":  ["fined", "penalty", "probe", "regulator", "antitrust", "watchdog", "sec", "ftc",
                   "bußgeld", "kartellamt", "bafin", "behörde"],
    "fraud":      ["fraud", "accounting", "embezzle", "bribery", "corruption", "betrug", "bestechung", "korruption"],
    "breach":     ["data breach", "breach", "hackers", "hacked", "hack", "cyberattack", "leak", "leaked", "ransomware",
                   "datenleck", "hackerangriff", "cyberangriff"],
    "boycott":    ["boycott", "backlash", "outrage", "shitstorm", "boykott", "empörung"],
    "leadership": ["resigns", "resignation", "ousted", "steps down", "fired ceo", "rücktritt", "tritt zurück"],
    "labor":      ["layoffs", "lays off", "job cuts", "strike", "walkout", "stellenabbau", "entlassungen", "streik"],
    "product":    ["recall", "defect", "faulty", "rückruf", "panne", "mangel"],
    "finance":    ["bankruptcy", "insolvency", "shares plunge", "shares tumble", "stock plunges", "insolvenz", "pleite"],
    "outage":     ["outage", "down for", "ausfall", "störung"],
}

BRAND_CATEGORIES = {
    "tech":          ["apple", "google", "alphabet", "meta", "facebook", "instagram", "microsoft", "amazon",
                      "openai", "tiktok", "software", "app", "platform", "social media", "chip", "ai",
                      "artificial intelligence", "samsung", "nvidia", "sap"],
    "auto":          ["car", "cars", "carmaker", "automaker", "vehicle", "vehicles", "volkswagen", "vw", "bmw",
                      "mercedes", "porsche", "audi", "tesla", "toyota", "ford", "stellantis", "autobauer"],
    "finance":       ["bank", "banks", "crypto", "insurer", "insurance", "fintech", "payments", "deutsche bank",
                      "ubs", "commerzbank", "wirecard", "versicherer"],
    "retail":        ["retailer", "store", "stores", "supermarket", "walmart", "target", "ikea", "aldi", "lidl",
                      "rewe", "edeka", "zalando", "händler"],
    "food & drink":  ["restaurant", "mcdonald", "starbucks", "coca-cola", "pepsi", "nestle", "nestlé", "burger",
                      "beverage", "brewer", "danone", "unilever"],
    "pharma":        ["drug", "drugs", "pharma", "vaccine", "medication", "pfizer", "bayer", "novartis", "roche",
                      "moderna", "medical device", "medikament"],
    "energy":        ["oil", "gas", "energy", "utility", "shell", "bp", "exxon", "chevron", "eon", "rwe", "uniper"],
    "travel":        ["airline", "airport", "flight", "flights", "lufthansa", "ryanair", "easyjet", "boeing",
                      "airbus", "hotel", "deutsche bahn", "bahn", "cruise"],
    "media":         ["netflix", "disney", "streaming", "broadcaster", "telecom", "telekom", "vodafone",
                      "spotify", "warner"],
    "fashion":       ["fashion", "clothing", "cosmetics", "beauty", "adidas", "nike", "puma", "shein", "h&m",
                      "zara", "primark"],
}

# words that never identify WHICH story it is (so grouping keys on company names)
BRAND_GENERIC = ni.GENERIC | set("""
scandal scandals lawsuit lawsuits sued sues class action boycott boycotts backlash outrage fraud
probe investigation investigated fined fine fines penalty settlement data breach hacked hack
cyberattack leak leaked recall resigns resignation ousted steps step down layoffs lays job cuts
strike walkout bankruptcy insolvency accused allegations alleged misconduct cover whistleblower
antitrust banned controversy apologizes apologises apology crisis outage shares plunge tumble
stock plunges fire under faces criticism company firm brand corp inc ltd plc maker giant ceo chief
executive shareholders investors customers users regulator regulators watchdog employees workers
staff billion million amid after over says report reports new
skandal klage verklagt sammelklage boykott shitstorm empörung betrug ermittlungen ermittelt bußgeld
strafe datenleck hackerangriff cyberangriff rücktritt stellenabbau entlassungen streik insolvenz
pleite vorwürfe vorwurf krise panne ausfall kartellamt razzia unternehmen konzern firma hersteller
händler kette vorstand konzernchef aktie aktionäre anleger kunden nutzer behörde mitarbeiter
milliarden millionen wegen nach gegen
""".split())

# Title-case headline words that are not company names
_NOT_NAMES = set("""
the a an and or of for to in on at by with from as is are was were be been it its this that these
those after amid over under into up out off new old more most says said say report reports how why
what who when where will would could should may might can could not no yes first last top big small
million billion thousand hundred year years month week day days today news update latest live
monday tuesday wednesday thursday friday saturday sunday january february march april may june july
august september october november december us usa uk eu europe european america american british
german germany world global national federal state states city chief ceo boss head group inc corp
ltd plc ag gmbh co company firm brand customers users investors workers staff employees shares stock
market markets price prices deal deals plan plans case cases court judge lawsuit probe recall fine
fined fines data breach hack strike boycott scandal crisis outage faces face hit hits sues sued
der die das und mit von für bei nach gegen wegen über auf neue neuer chef konzern kunden aktie
""".split())

KNOWN_BRANDS = {w for kws in BRAND_CATEGORIES.values() for w in kws if len(w) > 2 and w[0].isalpha()} - {
    "car", "cars", "bank", "banks", "store", "stores", "drug", "drugs", "oil", "gas", "app", "chip", "hotel",
    "flight", "flights", "airline", "airport", "fashion", "clothing", "beauty", "cosmetics", "energy",
    "utility", "vehicle", "vehicles", "retailer", "supermarket", "restaurant", "burger", "beverage",
    "brewer", "insurer", "insurance", "fintech", "payments", "platform", "software", "streaming",
    "broadcaster", "telecom", "pharma", "vaccine", "medication", "crypto", "carmaker", "automaker",
    "social media", "artificial intelligence", "medical device", "cruise", "autobauer", "händler",
    "versicherer", "medikament", "bahn"}

_KNOWN_LIST = sorted(KNOWN_BRANDS)
# words that may be part of a name ("American Express", "German Telekom") — kept inside names
_NAME_OK = {"american", "british", "german", "european", "national", "federal", "world", "global",
            "united", "general", "first", "new"}
_STRIP = _NOT_NAMES - _NAME_OK
_ALL = SCANDAL_TERMS + CORPORATE_TERMS + EXCLUDE_TERMS + list(BRAND_SEVERITY) + \
    [w for v in BRAND_KINDS.values() for w in v] + [w for v in BRAND_CATEGORIES.values() for w in v]
_SEV_RX = {k: re.compile(r"\b" + re.escape(k) + r"\b") for k in BRAND_SEVERITY}


def _strict(text):
    """A scandal word plus a clear company signal."""
    return ni._has_any(text, SCANDAL_TERMS) and (ni._has_any(text, CORPORATE_TERMS)
                                                 or ni._has_any(text, _KNOWN_LIST))


def _brand_relevant(text, title):
    """Kept for the arena: strict articles, plus scandal headlines that name someone —
    the latter survive only if a strict article names the same company (see collect_arena)."""
    if ni._has_any(text, EXCLUDE_TERMS):
        return False
    if ROUNDUP_RX.search(title or ""):
        return False
    if ni._has_any(text, POLITICS_TERMS) and not (ni._has_any(text, STRONG_CORPORATE)
                                                  or ni._has_any(text, _KNOWN_LIST)):
        return False
    if ni.FOOD_PROFILE["relevant"](text, title):       # food safety → food arena
        return False
    if _strict(text):
        return True
    return ni._has_any(text, SCANDAL_TERMS) and bool(names_in(title))


def _brand_severity(text):
    pts = sum(w for k, w in BRAND_SEVERITY.items() if _SEV_RX[k].search(text))
    return min(1.0, pts / 6.0)


def _brand_kinds(text):
    return {k for k, kws in BRAND_KINDS.items() if ni._has_any(text, kws)}


def _brand_categories(text):
    return {c for c, kws in BRAND_CATEGORIES.items() if ni._has_any(text, kws)}


def _brand_tokens(title):
    return ni._story_tokens(title, BRAND_GENERIC)


def _gn_q(q, region):
    return (ni._gn(q, region), "EU" if region in ("DE", "AT", "IE") else region)


BRAND_FEEDS = [
    _gn_q("company+scandal+OR+%22under+fire%22", "US"),
    _gn_q("%22class+action%22+OR+lawsuit+company", "US"),
    _gn_q("boycott+brand+OR+backlash+company", "US"),
    _gn_q("%22data+breach%22+customers", "US"),
    _gn_q("CEO+resigns+OR+ousted+OR+%22steps+down%22", "US"),
    _gn_q("fined+regulator+company+OR+antitrust", "US"),
    _gn_q("company+scandal+OR+fined+OR+boycott", "UK"),
    _gn_q("Skandal+Konzern+OR+Unternehmen", "DE"),
    _gn_q("Klage+OR+Sammelklage+Konzern", "DE"),
    _gn_q("Boykott+OR+Shitstorm+Marke", "DE"),
    _gn_q("Datenleck+OR+Hackerangriff+Kunden", "DE"),
    _gn_q("R%C3%BCcktritt+Konzernchef+OR+Vorstand", "DE"),
]

BRAND_PROFILE = {
    "name": "brands",
    "feeds": BRAND_FEEDS,
    "relevant": _brand_relevant,
    "tokens": _brand_tokens,
    "kinds": _brand_kinds,
    "categories": _brand_categories,
    "severity": _brand_severity,
}

ARENAS = {"brands": BRAND_PROFILE}          # "everyday" is added at the bottom (engine/everyday.py)
ARENA_LABELS = {"food": "Food safety", "brands": "Brand scandals", "everyday": "Food & drink"}
# arenas that only keep their already-listed stories up to date (no news pulled, nothing new listed)
MAINTENANCE_ONLY = {"brands"}


# ─────────────────────────────────────────────
# COMPANY NAME — which company is the story about
# ─────────────────────────────────────────────

_CAP_RX = re.compile(r"[A-ZÄÖÜ][\w&'.\-]*(?:\s+(?:&\s+)?[A-ZÄÖÜ][\w&'.\-]*){0,4}")


def _clean_name(raw):
    """Trim a capitalised run to a plausible company name, or None."""
    name = re.sub(r"['’]s$", "", raw.strip(" .-")).strip(" .'-")
    parts = [p for p in name.split() if not p.lower().endswith("-based")]
    while parts and parts[0].lower().strip(".,:'?!") in _LEAD_STRIP:        # small/common words at the front
        parts.pop(0)
    while parts and parts[-1].lower().strip(".,:'?!") in (_STRIP | COMMON_WORDS):   # generic words at the end
        parts.pop()
    parts = [p for p in parts if not _ROAD_RX.match(p.strip(".,:'"))] if len(parts) == 1 else parts
    if not parts:
        return None
    key = " ".join(parts).lower()
    if all(p.lower().strip(".,:'?!") in (COMMON_WORDS | _NOT_NAMES) for p in parts):   # "Users Report App"
        return None
    if len(parts) == 1 and (key in _NOT_NAMES or key in _NAME_OK or key in PLACES or _ROAD_RX.match(key)):
        return None
    if key in BRAND_GENERIC or key in PLACES or len(key) < 2:
        return None
    if all(p.lower() in PLACES or p.lower() in _NOT_NAMES for p in parts):
        return None
    return " ".join(parts)


def names_in(title):
    """Candidate company names in one headline (lower-case keys)."""
    out = set()
    for m in _CAP_RX.finditer(title or ""):
        name = _clean_name(m.group(0))
        if not name:
            continue
        out.add(name.lower())
        parts = name.split()
        if len(parts) > 1:                       # "Volkswagen Owners" also counts as "volkswagen"
            for p in parts:
                if p.lower() in KNOWN_BRANDS:
                    out.add(p.lower())
    return out


def same_brand_story(a, b):
    """Brand stories are about a company: same named company + the same kind of trouble
    (or no kind detected) → same story. Otherwise fall back to the word-overlap rule."""
    if ni._brand_clash(a, b):                     # different brands are never one story
        return False
    na, nb = a.get("names") or set(), b.get("names") or set()
    if na & nb:
        # "backlash" is a reaction, not a subject — it never separates two articles
        ka, kb = set(a["hazards"]) - {"backlash"}, set(b["hazards"]) - {"backlash"}
        if not ka or not kb or (ka & kb):
            return True
    return ni._same_story(a, b)


def extract_company(titles):
    """Most likely company name across a story's headlines (or None).
    Counts capitalised names that recur across headlines; names written capitalised in
    a sentence-case headline (a strong proper-noun signal) and known brands get a bonus."""
    score, shown = Counter(), {}
    for t in titles or []:
        words = t.split()
        lower_share = sum(1 for w in words if w[:1].islower()) / max(1, len(words))
        sentence_case = lower_share >= 0.35
        seen = set()
        for m in _CAP_RX.finditer(t):
            name = _clean_name(m.group(0))
            if not name:
                continue
            parts = name.split()
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            if not sentence_case and len(parts) > 2:      # Title Case run → keep the head of it
                parts = parts[:2]
                name, key = " ".join(parts), " ".join(parts).lower()
                if key in seen:
                    continue
            w = 2.0 if sentence_case and m.start() > 0 else 1.0
            if m.start() < len(t) * 0.45:          # names near the front are usually the subject
                w += 0.5
            if key in KNOWN_BRANDS or any(p.lower() in KNOWN_BRANDS for p in parts):
                w += 1.5
            score[key] += w
            shown.setdefault(key, name)
    if not score:
        return None
    key, best = score.most_common(1)[0]
    if best < 2.0 and len(titles or []) > 1:
        return None
    return shown[key]


# ─────────────────────────────────────────────
# COLLECT
# ─────────────────────────────────────────────

def maintain_arena(name):
    """No new news: listed stories fade from their stored articles and close on time."""
    from db.database import SessionLocal
    from engine.story_listing import update_listings
    session = SessionLocal()
    try:
        update_listings([], session, arena=name)
    finally:
        session.close()


def collect_arena(name, profile):
    from db.database import SessionLocal
    from db.models import ArenaReading
    from engine.story_listing import update_listings
    articles, stats = ni.pull_rss_articles(profile)
    if name == "everyday":
        from engine.everyday import pull_trending
        seen = {(a["title"].lower(), a["source"].lower()) for a in articles}
        trending, tstats = pull_trending(seen)
        articles += trending
        stats = {**stats, **tstats, "feeds_ok": stats.get("feeds_ok", 0) + tstats.get("trends_ok", 0)}
    if stats.get("feeds_ok", 0) == 0:
        print(f"⏭️ [{name}] no feeds reachable — reading skipped")
        return None
    for a in articles:
        a["names"] = names_in(a.get("raw_title") or a["title"])
    if name == "everyday":
        from engine.everyday import brand_in
        for a in articles:
            a["brand"] = brand_in([a.get("raw_title") or a["title"]])
    if name == "brands":
        # headlines without a company word stay only if a clear company story names the same company
        strict = [a for a in articles if _strict((a.get("raw_title") or a["title"]).lower())]
        known = set().union(*(a["names"] for a in strict)) if strict else set()
        articles = [a for a in articles if a in strict or (a["names"] & known)]
    stories = ni.group_stories(articles, same=same_brand_story if name in ("brands", "everyday") else None)
    if name == "everyday":
        try:
            from engine.everyday import reach_check
            extra = reach_check(stories, articles)
            if extra:
                from engine.everyday import brand_in
                for a in extra:
                    a["names"] = names_in(a["raw_title"])
                    a["brand"] = brand_in([a["raw_title"]])
                articles = articles + extra
                stories = ni.group_stories(articles, same=same_brand_story)
        except Exception as e:
            print(f"⚠️ [everyday] reach check error: {e}")
    by_title = {}
    for a in articles:
        by_title.setdefault(a.get("raw_title") or a["title"], a)
    for s in stories:
        s["arena"] = name
        s["company"] = extract_company(s.get("raw_titles"))
        if name == "everyday":
            from engine.everyday import sector_of, brand_in
            # a known brand in the headlines always wins; a name guessed from a single
            # headline ("Farm", "Parents"…) is too unreliable to keep
            s["company"] = brand_in(s.get("raw_titles")) or (s["company"] if len(s.get("raw_titles") or []) >= 2 else None)
            s["sector"] = sector_of(" ".join(s.get("raw_titles") or []).lower())
            tr = [by_title[t] for t in s.get("raw_titles") or [] if t in by_title and by_title[t].get("trend")]
            if tr:
                top = max(tr, key=lambda a: a.get("traffic") or 0)
                s["trending"] = {"term": top["trend"], "traffic": top.get("traffic") or 0}
    session = SessionLocal()
    try:
        ni.assign_story_ids(stories, session, arena=name)
        try:
            update_listings(stories, session, arena=name)
        except Exception as e:
            print(f"⚠️ [{name}] listing error: {e}")
        idx = ni.compact_story_index(stories)
        session.add(ArenaReading(arena=name, ts=time.time(), story_index=json.dumps(idx),
                                 meta=json.dumps({**stats, "stories": len(stories)})))
        session.commit()
        top = sorted(stories, key=lambda x: x["story_score"], reverse=True)[:3]
        print(f"🏷️ [{name}] {len(articles)} articles → {len(stories)} stories")
        for s in top:
            print(f"   • {s['story_score']:.1f} [{s['outlets']} outlets] {s.get('company') or '—'} · {s['title'][:70]}")
        return stories
    finally:
        session.close()


def all_arenas():
    """ARENAS with the everyday arena loaded (it imports this module, so it registers late)."""
    if "everyday" not in ARENAS:
        _register_everyday()
    return ARENAS


def collect_all_arenas():
    for name, profile in list(all_arenas().items()):
        try:
            if name in MAINTENANCE_ONLY:
                maintain_arena(name)
            else:
                collect_arena(name, profile)
        except Exception as e:
            print(f"❌ [{name}] arena collector error: {e}")
    try:
        from engine.behaviour import run_behaviour_checks
        run_behaviour_checks()
    except Exception as e:
        print(f"❌ behaviour recorder error: {e}")
    try:
        from engine.social import run_social_checks
        run_social_checks()
    except Exception as e:
        print(f"❌ social buzz recorder error: {e}")


def _register_everyday():
    try:
        from engine.everyday import EVERYDAY_PROFILE
        ARENAS["everyday"] = EVERYDAY_PROFILE
    except ImportError:
        pass          # module still loading (circular import) — all_arenas() retries later
    except Exception as e:
        print(f"⚠️ everyday arena not loaded: {e}")


_register_everyday()
