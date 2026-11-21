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
    # how people complain on Reddit / social media
    "disgusting", "gross", "overpriced", "ridiculous", "insane", "absurd", "unacceptable", "terrible",
    "awful", "trash", "garbage", "ruined", "ruin", "downgrade", "downgraded", "cheaped out", "skimping",
    "skimpy", "stingy", "smaller", "shrunk", "shrinking", "tiny portion", "half the size", "not worth",
    "highway robbery", "robbery", "bring back", "used to be", "what happened to", "worst",
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
    "jersey mike's", "jimmy john's", "firehouse subs", "wingstop", "raising cane's", "in-n-out", "whataburger",
    "culver's", "zaxby's", "cracker barrel", "texas roadhouse", "red lobster", "buffalo wild wings", "chili's",
    "outback steakhouse", "cheesecake factory", "dutch bros", "crumbl", "sweetgreen", "cava", "panda express",
    "little caesars", "papa murphy's", "qdoba", "del taco", "carl's jr", "hardee's", "dairy queen", "baskin-robbins",
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
# 21 Nov 2026: the brands that pull attention — creator & celebrity food brands, viral snacks and
# drinks, cult US chains and big packaged-food names that were missing. Ambiguous words are left out
# or written in full ("tyson foods", not "tyson"; "ritz crackers", not "ritz").
ATTENTION_BRANDS = [
    "feastables", "lunchly", "mrbeast burger", "prime energy", "logan paul's prime", "ksi's prime",
    "liquid death", "olipop", "poppi", "celsius energy", "alani nu", "ghost energy", "bang energy", "c4 energy",
    "takis", "hot cheetos", "sour patch kids", "swedish fish", "trolli", "goldfish crackers", "lunchables",
    "uncrustables", "kool-aid", "capri sun", "gatorade", "mountain dew", "dr pepper", "sprite", "fanta",
    "tropicana", "vitaminwater", "dasani", "aquafina", "lacroix", "la croix", "white claw", "modelo",
    "coors", "miller lite", "michelob ultra", "busch light", "jack daniel's", "fireball whisky", "tito's",
    "casamigos", "teremana", "general mills", "cheerios", "lucky charms", "froot loops", "cap'n crunch",
    "quaker oats", "tyson foods", "oscar mayer", "hormel", "smucker's", "jif", "skippy", "campbell's",
    "chobani", "fairlife", "boar's head", "blue bell", "hostess", "little debbie", "nabisco", "ritz crackers",
    "chips ahoy", "frito-lay", "tostitos", "ruffles", "kraft heinz", "hellmann's", "kirkland signature",
    "great value", "dave's hot chicken", "cook out", "wawa", "sheetz", "buc-ee's", "portillo's",
    "chuck e. cheese", "dave & buster's", "hooters", "tgi fridays", "red robin", "noodles & company",
    "bojangles", "el pollo loco", "church's chicken", "krystal", "steak 'n shake", "tropical smoothie",
    "jamba", "smoothie king", "insomnia cookies", "nothing bundt cakes", "magnolia bakery", "salt bae",
    "nusr-et", "hellofresh", "factor meals", "huel", "athletic greens", "ag1", "bloom nutrition",
]
FOOD_BRANDS += [b for b in ATTENTION_BRANDS if b not in FOOD_BRANDS]

GROCERS = ["walmart", "target", "costco", "aldi", "lidl", "tesco", "sainsbury's", "asda", "morrisons",
           "waitrose", "m&s", "marks & spencer", "kroger", "safeway", "whole foods", "trader joe's",
           "co-op", "iceland", "ocado", "publix", "h-e-b", "wegmans", "albertsons", "sam's club", "meijer", "food lion", "stop & shop", "sprouts farmers market",
           "winco", "hy-vee", "harris teeter", "ralphs", "vons", "dollar general", "dollar tree", "7-eleven"]
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


# a food brand's name on a stadium, team or race ("Kroger Field", "Red Bull Salzburg")
VENUE_RX = re.compile(r"\b(?:field|stadium|arena|center|centre|ballpark|bowl|park|garden|amphitheat(?:er|re)|speedway)\b|"
                      r"\bred bull (?:salzburg|leipzig|racing|bragantino|ring)\b|\bny red bulls\b|\bnew york red bulls\b", re.I)
SPORT_TERMS = ["betting tips", "betting odds", "lineups", "line-ups", "kickoff", "kick-off", "quarterback", "head coach", "coach",
               "playoff", "playoffs", "touchdown", "nfl", "nba", "mlb", "nhl", "ncaa", "premier league", "bundesliga",
               "champions league", "grand prix", "formula 1", "athletic director", "football", "basketball", "baseball",
               "soccer", "hockey", "tournament", "championship", "matchday", "fixture", "sec", "ole miss",
               "athletic", "athletics", "big ten", "big 12", "acc", "bowl game", "season opener"]
# crimes, missing people, deaths — real private people; never something to bet on
CRIME_TERMS = ["went missing", "goes missing", "gone missing", "reported missing", "missing driver", "missing person",
               "missing woman", "missing man", "missing teen", "missing child", "murder", "murdered", "killed", "killing", "stabbing", "stabbed", "assault", "assaulted",
               "arrested", "arrest", "charged with", "sentenced", "police", "homicide", "manslaughter", "kidnapped",
               "abducted", "rape", "sexual", "body found", "funeral", "last messages", "died", "dies", "dead", "death",
               "armed robbery", "gunman", "suspect", "victim", "court case"]
# tech / IT news that only mentions a food brand
OFFTOPIC_TERMS = ["payroll", "workday", "cyberattack", "cyber attack", "hackers", "data breach",
                  "ransomware", "nvidia", "jensen huang", "openai", "chatgpt", "bitcoin", "crypto", "semiconductor"]


# ── damage potential: can this story cost the company money? ──
# In the 16-case back-test, money was lost through price anger, safety scares, broken trust and
# boycotts — never through a funny new item or a nostalgic "bring it back".
STAKES_HIGH = ["price hike", "price increase", "prices", "overpriced", "overcharg", "hidden fee", "fees", "surcharge",
               "shrinkflation", "smaller", "less food", "portion", "rip-off", "ripoff", "scam", "boycott", "boycotting",
               "recall", "recalled", "sick", "illness", "contaminat", "e. coli", "salmonella", "listeria", "toxic",
               "unsafe", "health", "cancer", "chemical", "lawsuit", "sued", "class action", "lied", "lying",
               "misleading", "deceptive", "fake", "false advertising", "strike", "walkout", "union", "layoffs",
               "closing", "closures", "quit", "never again", "switching to", "done with"]
STAKES_LOW = ["discontinued", "bring back", "want back", "wants back", "nostalgia", "nostalgic", "joking", "jokes",
              "roasted", "roast", "meme", "memes", "funny", "hilarious", "wheezing", "limited edition", "limited-time",
              "new flavor", "collab", "collaboration", "merch", "holiday menu", "taste test", "we tried", "review",
              "ranked", "secret menu", "hack"]


# ── DEMAND: is somebody asking the brand to DO something? ──
# "Will they cave?" only makes sense when there is something to cave to. Stories without a
# demand (jokes, listicles, taste tests, "what's in this dip") are not listed, however much
# coverage they get.
_CHANGE = r"(?:price|prices|\$\d|smaller|portion|shrink|recipe|removed|remove|discontinu|policy|fee|fees|surcharge|charge|tipping|tip |menu change|cut |cuts |downsiz|replac)"
DEMAND_RX = re.compile(
    r"\bbring(?:s|ing)? (?:\w+ ){0,3}back\b|\b(?:want|wants|wanted|beg|begs|begging|plead\w*|demand\w*|asking|ask)\b.{0,40}\bback\b"
    r"|\bpetition|\bboycott|\brefund|\bapolog|\bdemand(?:s|ing|ed)?\b|\bcalls? (?:on|for)\b|\burg(?:e|es|ing)\b"
    r"|\bprotest|\bwalkout|\bwalk out\b|\bstrike\b|\blawsuit|\bclass action|\bsue[sd]?\b|\bsuing\b|\bthreaten\w* to (?:cancel|quit|boycott|leave|stop)\b"
    r"|\brevers(?:e|es|ed|al)\b|\bbacktrack|\bu-turn|\bscrap(?:s|ped)?\b|\bnever again\b|\bdone with\b|\bswitching to\b"
    r"|\b(?:price hike|price increase|raises prices|raising prices|shrinkflation|hidden fee|junk fee|fee hike|fee increase|membership fee)"
    r"|\b(?:backlash|outrage\w*|furious|angry|anger|slam\w*|complain\w*|fume\w*|livid|blast\w*|rip-?off)\b.{0,60}" + _CHANGE
    + r"|" + _CHANGE + r".{0,60}\b(?:backlash|outrage\w*|furious|angry|anger|slam\w*|complain\w*|fume\w*|livid|blast\w*)\b",
    re.I)
STRONG_DEMAND_RX = re.compile(r"\bpetition|\bboycott|\blawsuit|\bclass action|\bsued\b|\bwalkout|\bstrike\b", re.I)
JOKE_RX = re.compile(r"\bjok(?:e|es|ing)\b|\broast(?:ed|ing)?\b|\bmemes?\b|\bhilarious\b|\bwheezing\b|\bwe tried\b|"
                     r"\btaste test\b|\branked\b|\breview\b|\bdisadvantages\b|\breasons? (?:why|to)\b|\bwhat,? exactly\b|"
                     r"\bsecret menu\b|\bhacks?\b|\bis (?:the )?new .{0,60} good\b|^\d+ |"
                     r"\bupcoming\b|\bis back\b|\bare back\b|\bmakes? (?:a|its) return\b|\breturns? (?:to|for|this)\b|"
                     r"\bnostalgi\w*|\blaunch(?:es|ed|ing)?\b|\bdebuts?\b|\bunveil\w*|\bsneak peek\b|\bfans? (?:are )?(?:excited|thrilled|rejoice)", re.I)


def demand(lead, others=()):
    """The demand phrase found ('' if none). A joke / listicle lead headline needs a strong demand
    (boycott, petition, lawsuit, strike) to count."""
    lead = (lead or "").replace("’", "'")
    texts = [lead] + [(t or "").replace("’", "'") for t in others or []]
    if JOKE_RX.search(lead):
        for t in texts:
            m = STRONG_DEMAND_RX.search(t)
            if m:
                return m.group(0).strip()
        return ""
    for t in texts:
        m = DEMAND_RX.search(t)
        if m:
            return m.group(0).strip()[:60]
    return ""


# ── ONE GATE ──────────────────────────────────────────────────────────────
# Every rule that decides whether a story is "real US food-brand pressure" lives here, and the
# same function is used for listing, for the Food Reputation Index and for the moderator list.
# Change a rule here and it changes everywhere; tests/test_gate.py holds every headline we have
# argued about, good and bad — run it after any change.
NON_US_BRANDS = ["greggs", "pret", "costa", "nando's", "wetherspoons", "gail's", "itsu", "wagamama", "leon",
                 "pizza express", "frankie & benny's", "harvester", "toby carvery", "tesco", "sainsbury's", "asda",
                 "morrisons", "waitrose", "m&s", "marks & spencer", "co-op", "iceland", "ocado", "walkers",
                 "deliveroo", "just eat", "milka", "innocent", "rewe", "edeka", "carrefour", "woolworths", "coles",
                 "gokul", "amul", "jollibee", "loblaws", "metro", "migros", "mercadona"]
FOREIGN_RX = re.compile(
    r"£|€|₹|\bA\$|\bC\$|\buk\b|\bu\.k\.|\bbritain\b|\bbritish\b|\bengland\b|\benglish\b|\blondon\b|\bscotland\b|"
    r"\bscottish\b|\bwales\b|\bwelsh\b|\bireland\b|\birish\b|\bnhs\b|\bcouncil\b|\bhigh street\b|\bcrisps\b|"
    r"\baustralia\w*|\baussie\b|\bnew zealand\b|\bcanad\w+|\bontario\b|\btoronto\b|\bphilippine\w*|\bmanila\b|"
    r"\beurope\w*|\bgerman\w*|\bfrance\b|\bfrench\b|\bital(?:y|ian)\b|\bspain\b|\bspanish\b|\bdutch\b|\bswiss\b|"
    r"\bjapan\w*|\bchina\b|\bchinese\b|\bkorea\w*|\bsingapore\b|\bmalaysia\w*|\bindonesia\w*|\bpakistan\w*|"
    r"\bnigeria\w*|\bkenya\w*|\bsouth africa\w*|\bdubai\b|\bsaudi\b|\bmexico\b|\bbrazil\w*|\bsalzburg\b|\bcortina\b|"
    r"\bchairman\b.{0,30}\bmilk\b|\bdairy (?:co-?op|union|federation)\b",
    re.I)
US_RX = re.compile(
    r"\$\d|\bu\.s\.|(?-i:\bUS\b)(?! Open)|(?-i:\bUSA\b)|\bamerica\w*|\bfda\b|\busda\b|\bftc\b|\bcongress\b|\bwhite house\b|"
    r"\b(?:alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|"
    r"illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|"
    r"missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|"
    r"ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|"
    r"virginia|washington|west virginia|wisconsin|wyoming|chicago|los angeles|manhattan|brooklyn|houston|"
    r"atlanta|seattle|boston|philadelphia|phoenix|dallas|miami|denver|detroit|nyc|las vegas|san francisco|san diego|"
    r"san antonio|san jose|austin|nashville|orlando|tampa|portland|new orleans|st\.? louis|minneapolis|baltimore|"
    r"cleveland|cincinnati|pittsburgh|kansas city|milwaukee|charlotte|sacramento|salt lake city|honolulu|"
    r"long island|silicon valley|bay area|midwest|southern california|socal|santa cruz|capitola|oakland|fresno|"
    r"long beach|albuquerque|tucson|el paso|fort worth|columbus|indianapolis|jacksonville|memphis|louisville|"
    r"raleigh|omaha|boise|anchorage|spokane|reno|savannah|newark|jersey city|hoboken|queens|the bronx|"
    r"staten island|harlem|beverly hills|malibu|palm beach|key west|napa|fort lauderdale|scottsdale|"
    r"bakersfield|stockton|riverside|irvine|anaheim|santa monica|pasadena|tulsa|wichita|des moines|madison|"
    r"green bay|ann arbor|grand rapids|buffalo, n\.?y|rochester, n\.?y|hartford|providence|burlington, vt)\b",
    re.I)


def us_brand(titles):
    """The brand named, if it is one sold in the US ('' for none / a UK-only brand)."""
    b = brand_in(titles)
    if not b:
        return ""
    return "" if b.lower().replace("’", "'") in NON_US_BRANDS else b


# ── STAKES: only stories where something real is on the line ──
# money (prices, fees, portions), safety (illness, contamination), workers (pay, union, strikes)
# or a boycott / broken trust. "Bring back my favourite sub" is nostalgia: nothing at stake.
STAKE_RX = {
    "money":   re.compile(r"\bprices?\b|\bpric(?:ing|ed)\b|\$\d|\bfees?\b|\bsurcharge|\bovercharg|\btip(?:s|ping)?\b|"
                          r"\bshrinkflation|\bsmaller\b|\bportions?\b|\bdownsiz|\bcost(?:s|ly)?\b|\bexpensive\b|"
                          r"\brip-?off\b|\bdiscount\b|\bmembership\b|\brefund|\bcharg(?:e|es|ed|ing)\b|\bdeal\b.{0,20}\b(?:cut|end|scrap)", re.I),
    "safety":  re.compile(r"\brecall|\bsick\b|\billness|\bpoison|\bcontaminat|\boutbreak|\be\.? ?coli\b|\bsalmonella|"
                          r"\blisteria|\btoxic|\bunsafe\b|\bhospitali|\ballerg|\bchemical|\bcancer\b|\bmold\b|\bforeign object", re.I),
    "workers": re.compile(r"\bunion|\bbaristas?\b|\bworkers?\b|\bemployees?\b|\bstaff\b|\bstrike\b|\bwalk ?out|\bwages?\b|"
                          r"\bpay\b|\blayoffs?\b|\bcontract\b|\bfranchisees?\b", re.I),
    "boycott / trust": re.compile(r"\bboycott|\bpetition|\blied\b|\blying\b|\bmisleading|\bdeceptive|\bfake\b|\bfalse advertising|"
                                  r"\blawsuit|\bsue[sd]?\b|\bsuing\b|\bclass action|\bscandal|\bcover-?up|\bpolicy\b|\bnever again\b|"
                                  r"\bdone with\b|\bswitching to\b|\bcancel(?:s|led|ing)? (?:their|my|our)?\s*(?:membership|subscription|order)", re.I),
}
NOSTALGIA_RX = re.compile(r"\bdiscontinued\b|\bbring(?:s|ing)? (?:\w+ ){0,3}back\b|\bwants? .{0,30}\bback\b|\bbeg\w*\b.{0,40}\bback\b|"
                          r"\bnostalgi|\bmissing\b.{0,20}\bmenu\b|\bcomeback\b", re.I)


def stake_of(title, others=()):
    """'money' / 'safety' / 'workers' / 'boycott / trust' — what is on the line ('' if nothing).
    Petitions to bring back a discontinued item are nostalgia, not a boycott."""
    t = (title or "").replace("’", "'")
    nost = bool(NOSTALGIA_RX.search(t))
    for cat, rx in STAKE_RX.items():
        m = rx.search(t)
        if m and not (nost and cat == "boycott / trust" and m.group(0).lower().startswith("petition")):
            return cat
    return ""


# ── NOT THE BRAND'S TROUBLE ──
# The brand is only a prop ("AI mistook a Doritos bag for a gun", "students sue school"), the story is
# an obituary ("Pizza Hut co-founder passes away"), or it is an investor piece ("Is Kroger undervalued?",
# "$1000 invested 20 years ago", securities class-action ads). Nothing the brand could back down on.
PROP_RX = re.compile(r"\bmist(?:ook|aken|akes?|aking)\b|\bconfused (?:\w+ ){0,3}(?:for|with)\b|"
                     r"\bfor a (?:gun|weapon|firearm|bomb)\b|\bthought (?:it )?was a (?:gun|weapon|bomb)\b", re.I)
SUE_OTHER_RX = re.compile(r"\bsu(?:e|es|ed|ing)\b (?:the |their |a )?(?:\w+ ){0,2}(?:school|district|police|cops|officers?|"
                          r"city|county|state|university|college|hospital|airline|landlord|employer|teacher|principal)\b", re.I)
# "a bag of Doritos" / "Doritos bag" at the centre of a security / police incident
_OBJECT_RX = re.compile(r"\b(?:bag|can|bottle|box|wrapper|packet|carton|cup)s? of [A-Z]|"
                        r"\b[A-Z][\w'’-]+ (?:bag|can|bottle|wrapper|packet|carton)s?\b(?! (?:fee|policy|ban|price|size))")
_INCIDENT_RX = re.compile(r"\b(?:AI|security|surveillance|police|cops|officers|gun|weapon|alert|handcuffed|arrested|swat)\b")
OBIT_RX = re.compile(r"\b(?:passe[sd] away|obituary|funeral|in memoriam|laid to rest)\b|"
                     r"\b(?:founder|co-?founder|creator|inventor|ceo|chairman|executive|former|legend|icon|mascot|actor|star)\b"
                     r".{0,60}\b(?:dies|died|dead)\b|\b(?:dies|died)\b.{0,15}\bat (?:age )?\d+|\b(?:dies|died)\b,? (?:aged|at) ", re.I)
INVESTOR_RX = re.compile(r"\bundervalued\b|\bovervalued\b|\bprice target\b|\bstock (?:forecast|rating|split|pick)|"
                         r"\b(?:buy|sell),? (?:sell,? )?or hold\b|\binvested in\b|\$\d[\d,]* invested\b|\bwould be worth\b|"
                         r"\bcash flow\b|\bvaluation\b|\bdividend|\bearnings (?:preview|call|estimate|beat|miss)|\bEPS\b|"
                         r"\bshares? (?:rise|rises|rose|fall|falls|fell|jump|jumps|jumped|drop|drops|dropped|slip|slips|climb|climbs)\b|"
                         r"\bstock (?:drop|decline|falls?|plunge)|\b\d+(?:\.\d+)?% stock\b|\bsecurities (?:fraud|class action|litigation)|"
                         r"\blead plaintiff\b|\binvestors? (?:may|should|who|are encouraged|know)\b|\bhedge fund|\bstake in\b", re.I)
_SECURITIES_RX = re.compile(r"\bsecurities (?:fraud|class action|litigation)|\blead plaintiff\b|\binvestors? (?:may|should|are encouraged|who (?:lost|bought|purchased))\b|"
                            r"\b(?:law (?:firm|offices?)|esq\.?|llp)\b.{0,80}\b(?:investors?|shareholders?|stock)\b", re.I)
_TICKER_RX = re.compile(r"\(\s*(?:NYSE|NASDAQ)?\s*:?\s*(?!(?:FDA|USDA|CDC|FTC|UFCW|SEIU|AI|US|USA|UK|PHOTOS?|VIDEO|WATCH)\s*\))[A-Z]{1,5}\s*\)")


def not_their_trouble(title):
    """Why the named brand is not the one under pressure ('' if it is)."""
    t = (title or "").replace("’", "'")
    if _SECURITIES_RX.search(t):
        return "investor article, not public pressure"
    if INVESTOR_RX.search(t) or _TICKER_RX.search(t):
        # "shares fall after boycott / recall / strike" is still public pressure; a valuation piece is not
        if not any(STAKE_RX[k].search(t) for k in ("safety", "workers", "boycott / trust")) \
                and not re.search(r"\b(?:backlash|outrage|furious|angry|slam\w*|fume\w*)\b", t, re.I):
            return "investor article, not public pressure"
    if OBIT_RX.search(t) and not STAKE_RX["safety"].search(t):
        return "obituary, nothing the brand could back down on"
    if PROP_RX.search(t) or SUE_OTHER_RX.search(t) or (_OBJECT_RX.search(t) and _INCIDENT_RX.search(t)):
        return "the brand is a bystander, someone else is blamed"
    return ""


def gate(title, others=(), company=None):
    """(ok, reason) — is this real pressure on a US food or drink brand?
    ok=False comes with the first rule it failed, in plain words."""
    title = (title or "").strip()
    texts = [title] + [t for t in (others or []) if t]
    low = title.lower()
    if not english_only(title):
        return False, "not English / not a US audience"
    if blocked_title(low, title):
        # a "deal" / "discount" word alone must not hide real anger ("removes discount, customers fume")
        only_deal = DEAL_RX.search(title) and not blocked_title(low, DEAL_RX.sub(" ", title))
        if not (only_deal and demand(title)):
            return False, "off-topic (deal, list, sport, crime, politics, tech…)"
    if POSITIVE_RX.search(title) and not ni._has_any(low, NEGATIVE_HINT):
        return False, "praise, not pressure"
    if QUOTE_PAGE_RX.search(title):
        return False, "finance quote page"
    why = not_their_trouble(title)
    if why:
        return False, why
    if FOREIGN_RX.search(title) and not US_RX.search(title):     # "U.S. and Canada" is still about the US
        return False, "not about the US"
    named = brand_in([title]) or company
    if named and named.lower().replace("’", "'") in NON_US_BRANDS:
        return False, f"{named} is not a US brand"
    if not (us_brand([title]) or (company and company.lower() not in NON_US_BRANDS and brand_in([company]))
            or US_RX.search(title)):
        return False, "no US brand or US place named"
    d = demand(title, others)
    if not d:
        return False, "no demand on the brand"
    cat = stake_of(title, others)
    if not cat:
        if NOSTALGIA_RX.search(title):
            return False, "nostalgia, nothing at stake"
        return False, "no real stakes (money, safety, workers or boycott)"
    return True, f"{d} · {cat}"


def index_gate(title, others=(), company=None):
    """(ok, reason) — does this story count in the Food Reputation Index? Broader than gate():
    US, a food business, and something real at stake (money, safety, workers, boycott / trust).
    No named brand or explicit demand needed; culture-war fights about a named food brand count.
    Jokes, listicles, promos, nostalgia and foreign stories stay out."""
    title = (title or "").strip()
    low = title.lower()
    if not english_only(title):
        return False, "not English / not a US audience"
    if blocked_title(low, title, allow_politics=True):
        only_deal = DEAL_RX.search(title) and not blocked_title(low, DEAL_RX.sub(" ", title), allow_politics=True)
        if not (only_deal and demand(title)):
            return False, "off-topic (deal, list, sport, crime, tech…)"
    if QUOTE_PAGE_RX.search(title):
        return False, "finance quote page"
    why = not_their_trouble(title)
    if why:
        return False, why
    if POSITIVE_RX.search(title) and not ni._has_any(low, NEGATIVE_HINT):
        return False, "praise, not pressure"
    if JOKE_RX.search(title) and not STRONG_DEMAND_RX.search(title):
        return False, "joke, review or promo"
    if FOREIGN_RX.search(title) and not US_RX.search(title):
        return False, "not about the US"
    named = brand_in([title]) or company
    if named and named.lower().replace("’", "'") in NON_US_BRANDS and not US_RX.search(title):
        return False, f"{named} is not a US brand"
    if not (us_brand([title]) or (company and company.lower() not in NON_US_BRANDS and brand_in([company]))
            or US_RX.search(title)):
        return False, "no US brand or US place named"
    if not (us_brand([title]) or company) and not (ni._has_any(low, FOOD_WORDS) or re.search(
            r"\brecall|\bgrocer|\brestaurant|\bdiner|\bfarm|\bmeat|\bbeef|\bchicken|\begg|\bmilk|\bproduce\b", low)):
        return False, "no brand and not about food"        # "beach reopens after E. coli" is water, not food
    cat = stake_of(title, others)
    if not cat:
        return False, "nostalgia, nothing at stake" if NOSTALGIA_RX.search(title) else "no real stakes"
    return True, cat


def stakes(texts):
    """('high' | 'normal' | 'low', multiplier) for a story's headlines."""
    t = " ".join(texts or []).lower().replace("’", "'")
    if ni._has_any(t, STAKES_HIGH):
        return "high", 1.25
    if ni._has_any(t, STAKES_LOW):
        return "low", 0.75
    return "normal", 1.0


# culture-war words: such stories can count in the index (when about a named food brand),
# but are never shown to players — moderation risk we decided not to take on for now
CULTURE_WAR = ["transgender", "trans activist", "trans influencer", "lgbtq", "lgbt", "pride month", "pride collection",
               "drag queen", "woke", "anti-woke", "dei", "diversity, equity", "conservative", "conservatives", "liberal",
               "liberals", "maga", "trump", "biden", "harris", "right-wing", "left-wing", "abortion", "gun rights",
               "immigration", "ice raids", "deportation", "culture war", "political"]
# violence stays out everywhere, even for the index
ALWAYS_BLOCK = ["shooting", "shot dead", "airstrike", "terrorist", "terrorism", "genocide", "massacre", "bombing"]


def blocked_title(text, title, allow_politics=False):
    """Shared hard filters: deals, list articles, politics, religion/ethnicity/war, sport, crime, tech news.
    allow_politics=True (index only): a culture-war or political fight ABOUT a named food brand is kept —
    it never reaches players, but it is real reputation pressure."""
    t = title or ""
    if DEAL_RX.search(t) or LISTICLE_RX.search(t):
        return True
    if VENUE_RX.search(t) and brand_in([t]):
        # "Kroger Field", "Pizza Hut Park" — the brand is a venue name here
        b = brand_in([t]).lower()
        if re.search(re.escape(b) + r"\W+(?:field|stadium|arena|center|centre|ballpark|bowl|park|garden|speedway)", t.lower()) \
                or re.search(r"red bull (?:salzburg|leipzig|racing|bragantino|ring)|red bulls", t.lower()):
            return True
    if ni._has_any(text, ALWAYS_BLOCK):
        return True
    politics = ni._has_any(text, A.HARD_POLITICS) or ni._has_any(text, A.SENSITIVE_TERMS)
    politics = politics or ni._has_any(text, CULTURE_WAR)
    if politics and allow_politics and brand_in([t]):
        politics = False
    # "listeria outbreak … after deaths", "dies after eating at …": illness from food is food safety, not crime
    crime = ni._has_any(text, CRIME_TERMS) and not STAKE_RX["safety"].search(t)
    return (politics or ni._has_any(text, SPORT_TERMS) or crime or ni._has_any(text, OFFTOPIC_TERMS))


# Tagalog (Filipino) headlines and Philippine TV/showbiz pages
_TL_RX = re.compile(r"\b(ng|mga|ang|sa|nang|kay|po|naman|lang|hindi)\b|\bGMA (?:Entertainment|News|Network)\b|"
                    r"\bABS-CBN\b|\bBubble Gang\b", re.I)


def english_only(title):
    """English, and meant for a US/UK/European audience."""
    t = title or ""
    return not _DE_RX.search(t) and not _IN_RX.search(t) and not _TL_RX.search(t)


# fan-favourite / praise stories ("the Costco deli find fans want all to themselves") are not drama
POSITIVE_RX = re.compile(r"\bfans? (?:love|loves|want|rave|are obsessed)|\bobsessed\b|\bbest (?:meals?|thing|item|snack|deal)|"
                         r"\bmust[- ]try\b|\bgame[- ]changer\b|\byou need to try\b|\bdelicious\b|\bhidden gem\b|"
                         r"\bfan[- ]favou?rite\b|\bnew find\b|\bfind that\b|\bwant all to themselves\b", re.I)
NEGATIVE_HINT = ["furious", "outrage", "outraged", "backlash", "boycott", "angry", "slam", "slammed", "complain",
                 "disgusting", "overpriced", "ripoff", "rip-off", "scam", "smaller", "shrink", "discontinued",
                 "worst", "terrible", "not worth", "price hike", "swearing off", "quit", "ditch"]


# finance pages that only name a brand ("Tesco PLC (TSCO.L) stock price, news, quote and history")
QUOTE_PAGE_RX = re.compile(r"\bstock (?:price|quote)s?\b.{0,40}\b(?:news|history|quote|chart)\b|\bquote and history\b|"
                           r"\(\s*[A-Z]{1,5}\.[A-Z]{1,2}\s*\)|\b(?:NYSE|NASDAQ|LSE|LON)\s*:\s*[A-Z]{1,5}\b.{0,30}\b(?:price|quote)\b")


def _everyday_relevant(text, title):
    if not english_only(title) or blocked_title(text, title, allow_politics=True):
        return False
    if QUOTE_PAGE_RX.search(title or ""):
        return False
    b = brand_in([title or ""])
    if b and b.lower().replace("’", "'") in NON_US_BRANDS and not US_RX.search(title or ""):
        return False                       # UK / foreign brands: not our market, don't spend checks on them
    if POSITIVE_RX.search(title or "") and not ni._has_any((title or "").lower(), NEGATIVE_HINT):
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


REDDIT_SUBS = ["fastfood", "mildlyinfuriating", "shrinkflation", "Costco", "traderjoes", "starbucks",
               "McDonalds", "Chipotle", "tacobell", "kroger", "walmart", "wholefoods", "Wendys", "BurgerKing",
               "Dunkin", "PaneraBread", "Frugal"]

EVERYDAY_FEEDS = [
    _gn("fast+food+customers+furious+OR+backlash+OR+outrage", "US"),
    _gn("%22menu+change%22+OR+%22recipe+change%22+OR+discontinued+fans+food+OR+drink", "US"),
    _gn("restaurant+chain+backlash+OR+boycott+OR+%22customers+slam%22", "US"),
    _gn("snack+OR+chocolate+OR+soda+discontinued+fans+OR+petition", "US"),
    _gn("grocery+prices+shoppers+furious+OR+shrinkflation+food", "US"),
    _gn("viral+food+OR+tiktok+food+backlash+OR+divided", "US"),
    _gn("%22ultra-processed%22+OR+%22seed+oils%22+OR+%22school+lunch%22+backlash+OR+debate", "US"),
    _gn("food+outbreak+blame+OR+denies+OR+%22families+sue%22+OR+reopens", "US"),
    _gn("%22price+hike%22+restaurant+OR+%22fast+food%22+OR+grocery+customers", "US"),
    _gn("shrinkflation+OR+%22smaller+portions%22+OR+%22less+food%22+customers", "US"),
    _gn("food+brand+%22misleading%22+OR+lawsuit+OR+%22class+action%22+customers", "US"),
    _gn("boycott+restaurant+OR+%22fast+food%22+OR+grocery+OR+snack+brand", "US"),
    ("https://www.eater.com/rss/index.xml", "US"),
    # sites that write up viral food drama ("fans furious", "TikToker exposes", "customers slam")
    ("https://www.thetakeout.com/feed/", "US"),
    ("https://www.mashed.com/feed/", "US"),
    ("https://www.tastingtable.com/feed/", "US"),
    ("https://www.eatthis.com/feed/", "US"),
    ("https://www.dailydot.com/feed/", "US"),
    _gn("site:dailydot.com+OR+site:today.com+OR+site:delish.com+food+OR+restaurant+fans+OR+customers+OR+tiktok", "US"),
    _gn("site:thetakeout.com+OR+site:mashed.com+OR+site:allrecipes.com+discontinued+OR+backlash+OR+furious", "US"),
    # Reddit — where people actually complain (today's top posts; free RSS)
    # all subreddits in ONE request — Reddit answers 429 to ten quick requests from one server
    ("https://www.reddit.com/r/" + "+".join(REDDIT_SUBS) + "/top/.rss?t=day&limit=100", "US"),
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

TRENDS_GEOS = [("US", "US")]                       # US focus (Nov 2026)
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
        low = t.lower().replace("’", "'").replace("‘", "'")      # "Wendy’s" → "wendy's" (same length)
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


# too general to tell one story about a brand from another ("McDonald's restaurant")
BUZZ_GENERIC = set("""
restaurant restaurants store stores shop shops chain chains location locations outlet outlets branch
branches brand brands company firm customer customers fan fans product products item items
move moves moving moved plan plans planned planning make makes making made major big huge biggest
latest report reports reported launch launches launched announce announces announced change changes
changing changed going goes come comes coming take takes taking get gets getting want wants
""".split())


def _norm_word(w):
    w = w.replace("’", "'").strip("'-")
    return re.sub(r"'s$", "", w).replace("'", "")


def topic_words(titles, company, n=2, exclude=()):
    """Words that say what the story is about (besides the brand), e.g. 'baggage fees'."""
    comp_raw = (company or "").lower().replace("’", "'")
    comp = set(re.findall(r"[a-z0-9]+", comp_raw)) | {_norm_word(x) for x in comp_raw.split()} \
        | {_norm_word(comp_raw).replace(" ", "")}
    counts, first = {}, {}
    for i, t in enumerate(titles or []):
        for j, w in enumerate(re.findall(r"[a-z][a-z0-9'’-]+", t.lower())):
            w = _norm_word(w)
            if not w or w in comp or w in _TOPIC_SKIP or w in exclude or (len(w) < 4 and w not in _TOPIC_SUBJECT):
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


DISCOVERY_SEARCHES = 3


def discovery_news(articles):
    """Brands rising on Bluesky → one Google News search each ("<brand> <words people use>").
    Matching headlines join the arena like any other article, so a story found on social media
    can be listed as soon as the news covers it."""
    from db.database import SessionLocal
    from engine.discovery import rising
    session = SessionLocal()
    try:
        hot = rising(session)
        try:                                   # big brand videos on YouTube pull the news search forward too
            from engine.attention import news_triggers
            have = {h["brand"].lower() for h in hot}
            hot = hot + [t for t in news_triggers(session) if t["brand"] not in have]
        except Exception as e:
            print(f"⚠️ youtube triggers: {e}")
    finally:
        session.close()
    seen = {(a["title"].lower(), a["source"].lower()) for a in articles}
    cutoff = time.time() - ni.ARTICLE_WINDOW_H * 3600
    extra, searched = [], []
    for r in hot[:DISCOVERY_SEARCHES + 2]:
        brand, words = r["brand"], r["words"][:2]
        query = f'"{brand}" ' + " ".join(words)
        searched.append(query)
        b_low = brand.lower()
        for it in _search(query):
            t, tl = it["title"], it["title"].lower().replace("’", "'")
            key = (it["title"].lower(), it["source"].lower())
            if key in seen or it["ts"] < cutoff or b_low not in tl:
                continue
            if words and not any(w in tl for w in words):
                continue
            if not english_only(t) or blocked_title(tl, t) or ni._has_any(tl, A.EXCLUDE_TERMS):
                continue
            seen.add(key)
            extra.append({
                "title": t, "source": it["source"], "url": it["url"], "ts": it["ts"],
                "region": ni._outlet_region(it["href"], "US"),
                "tokens": _everyday_tokens(t), "hazards": _everyday_kinds(tl),
                "categories": _everyday_categories(tl), "severity": _everyday_severity(tl),
                "negativity": ni._negativity(tl), "spread": ni._has_any(tl, ni.SPREAD_TERMS),
                "raw_title": t, "reach": True, "discovered": True,
            })
    if searched:
        print(f"🔎 [everyday] discovery news: {len(searched)} rising brands searched → {len(extra)} articles")
    return extra


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
