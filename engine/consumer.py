"""
Wider scope — listed US consumer brands (23 Nov 2026)
=====================================================
US food & drink alone produced about one real story every 4–5 days: too few to measure, list or sell.
The buyers we aim at (investors, insurers) care about listed consumer brands in general, so the scope
is widened to the brands ordinary Americans buy from every week, across seven sectors besides food:

  retail · fashion & beauty · travel · entertainment & streaming · cable & phones · cars · banks & payments

Every brand here is tracked in the track record (Wikipedia attention, share move against the market) and
counts in the index and the game under the same filters as food brands: US, real trouble for the brand,
no promos, no jokes. Video games, AI / chip makers and non-US brands stay out (noise, not our market).

brand → (sector, Wikipedia article, share ticker or None)
"""

WIDE_BRANDS = {
    # retail
    "amazon": ("retail", "Amazon_(company)", "AMZN"), "home depot": ("retail", "The_Home_Depot", "HD"),
    "lowe's": ("retail", "Lowe's", "LOW"), "best buy": ("retail", "Best_Buy", "BBY"),
    "macy's": ("retail", "Macy's", "M"), "kohl's": ("retail", "Kohl's", "KSS"),
    "dollar general": ("retail", "Dollar_General", "DG"), "dollar tree": ("retail", "Dollar_Tree", "DLTR"),
    "cvs": ("retail", "CVS_Pharmacy", "CVS"), "walgreens": ("retail", "Walgreens", None),
    "ulta": ("retail", "Ulta_Beauty", "ULTA"), "etsy": ("retail", "Etsy", "ETSY"), "ebay": ("retail", "EBay", "EBAY"),
    "wayfair": ("retail", "Wayfair", "W"), "temu": ("retail", "Temu", "PDD"), "shein": ("retail", "Shein", None),
    "chewy": ("retail", "Chewy_(company)", "CHWY"), "sam's club": ("retail", "Sam's_Club", "WMT"),
    # fashion & beauty
    "nike": ("fashion & beauty", "Nike,_Inc.", "NKE"), "lululemon": ("fashion & beauty", "Lululemon", "LULU"),
    "under armour": ("fashion & beauty", "Under_Armour", "UAA"), "crocs": ("fashion & beauty", "Crocs", "CROX"),
    "levi's": ("fashion & beauty", "Levi_Strauss_&_Co.", "LEVI"), "ralph lauren": ("fashion & beauty", "Ralph_Lauren_Corporation", "RL"),
    "gap": ("fashion & beauty", "Gap_Inc.", "GAP"), "old navy": ("fashion & beauty", "Old_Navy", "GAP"),
    "abercrombie": ("fashion & beauty", "Abercrombie_&_Fitch", "ANF"), "american eagle": ("fashion & beauty", "American_Eagle_Outfitters", "AEO"),
    "victoria's secret": ("fashion & beauty", "Victoria's_Secret", "VSCO"), "bath & body works": ("fashion & beauty", "Bath_&_Body_Works", "BBWI"),
    "estee lauder": ("fashion & beauty", "Estée_Lauder_Companies", "EL"), "e.l.f.": ("fashion & beauty", "E.l.f._Cosmetics", "ELF"),
    "sephora": ("fashion & beauty", "Sephora", None), "vans": ("fashion & beauty", "Vans", "VFC"),
    "the north face": ("fashion & beauty", "The_North_Face", "VFC"), "adidas": ("fashion & beauty", "Adidas", None),
    "skims": ("fashion & beauty", "Skims", None), "stanley": ("fashion & beauty", "Stanley_(drinkware)", None),
    # travel
    "delta air lines": ("travel", "Delta_Air_Lines", "DAL"), "united airlines": ("travel", "United_Airlines", "UAL"),
    "american airlines": ("travel", "American_Airlines", "AAL"), "southwest airlines": ("travel", "Southwest_Airlines", "LUV"),
    "jetblue": ("travel", "JetBlue", "JBLU"), "alaska airlines": ("travel", "Alaska_Airlines", "ALK"),
    "frontier airlines": ("travel", "Frontier_Airlines", "ULCC"), "spirit airlines": ("travel", "Spirit_Airlines", None),
    "marriott": ("travel", "Marriott_International", "MAR"), "hilton": ("travel", "Hilton_Worldwide", "HLT"),
    "hyatt": ("travel", "Hyatt", "H"), "airbnb": ("travel", "Airbnb", "ABNB"), "expedia": ("travel", "Expedia_Group", "EXPE"),
    "uber": ("travel", "Uber", "UBER"), "lyft": ("travel", "Lyft", "LYFT"),
    "carnival": ("travel", "Carnival_Corporation_&_plc", "CCL"), "royal caribbean": ("travel", "Royal_Caribbean_Group", "RCL"),
    "norwegian cruise line": ("travel", "Norwegian_Cruise_Line", "NCLH"), "hertz": ("travel", "Hertz_Global_Holdings", "HTZ"),
    # entertainment & streaming
    "disney": ("entertainment & streaming", "The_Walt_Disney_Company", "DIS"), "netflix": ("entertainment & streaming", "Netflix", "NFLX"),
    "hbo": ("entertainment & streaming", "HBO", "WBD"), "warner bros": ("entertainment & streaming", "Warner_Bros._Discovery", "WBD"),
    "spotify": ("entertainment & streaming", "Spotify", "SPOT"), "ticketmaster": ("entertainment & streaming", "Ticketmaster", "LYV"),
    "live nation": ("entertainment & streaming", "Live_Nation_Entertainment", "LYV"), "roku": ("entertainment & streaming", "Roku", "ROKU"),
    "peacock": ("entertainment & streaming", "Peacock_(streaming_service)", "CMCSA"), "hulu": ("entertainment & streaming", "Hulu", "DIS"),
    "paramount": ("entertainment & streaming", "Paramount_Skydance", None),
    # cable & phones (consumer tech)
    "apple": ("cable & phones", "Apple_Inc.", "AAPL"), "verizon": ("cable & phones", "Verizon", "VZ"),
    "at&t": ("cable & phones", "AT&T", "T"), "t-mobile": ("cable & phones", "T-Mobile_US", "TMUS"),
    "comcast": ("cable & phones", "Comcast", "CMCSA"), "xfinity": ("cable & phones", "Xfinity", "CMCSA"),
    "spectrum": ("cable & phones", "Charter_Communications", "CHTR"), "peloton": ("cable & phones", "Peloton_Interactive", "PTON"),
    "duolingo": ("cable & phones", "Duolingo", "DUOL"), "tinder": ("cable & phones", "Tinder_(app)", "MTCH"),
    # cars
    "tesla": ("cars", "Tesla,_Inc.", "TSLA"), "ford": ("cars", "Ford_Motor_Company", "F"),
    "general motors": ("cars", "General_Motors", "GM"), "chevrolet": ("cars", "Chevrolet", "GM"),
    "jeep": ("cars", "Jeep", "STLA"), "toyota": ("cars", "Toyota", "TM"), "honda": ("cars", "Honda", "HMC"),
    "harley-davidson": ("cars", "Harley-Davidson", "HOG"), "carvana": ("cars", "Carvana", "CVNA"),
    # banks & payments
    "wells fargo": ("banks & payments", "Wells_Fargo", "WFC"), "chase": ("banks & payments", "JPMorgan_Chase", "JPM"),
    "bank of america": ("banks & payments", "Bank_of_America", "BAC"), "capital one": ("banks & payments", "Capital_One", "COF"),
    "citibank": ("banks & payments", "Citibank", "C"), "paypal": ("banks & payments", "PayPal", "PYPL"),
    "venmo": ("banks & payments", "Venmo", "PYPL"), "cash app": ("banks & payments", "Cash_App", "XYZ"),
    "robinhood": ("banks & payments", "Robinhood_Markets", "HOOD"), "american express": ("banks & payments", "American_Express", "AXP"),
}
WIDE_ALIASES = {"delta": "delta air lines", "southwest": "southwest airlines", "united": None,
                "jpmorgan": "chase", "jpmorgan chase": "chase", "estée lauder": "estee lauder", "gm": "general motors",
                "home depot's": "home depot", "amazon prime": "amazon", "prime video": "amazon", "disney+": "disney",
                "disney plus": "disney", "iphone": "apple", "airpods": "apple", "ipad": "apple", "macbook": "apple",
                "hbo max": "hbo", "max": None, "warner bros. discovery": "warner bros", "abercrombie & fitch": "abercrombie",
                "elf cosmetics": "e.l.f.", "uber eats": None}

# brand names for detection that the old consumer list lacked
EXTRA_NAMES = ["macy's", "kohl's", "marriott", "hilton", "hyatt", "carnival", "royal caribbean", "norwegian cruise line",
               "wells fargo", "capital one", "citibank", "american express", "dollar general", "dollar tree", "wayfair",
               "chewy", "levi's", "ralph lauren", "abercrombie", "abercrombie & fitch", "american eagle",
               "bath & body works", "estee lauder", "estée lauder", "e.l.f.", "the north face", "delta air lines",
               "southwest airlines", "live nation", "warner bros", "hbo max", "general motors", "chevrolet", "jeep",
               "carvana", "t-mobile", "xfinity", "spectrum", "peloton", "hertz", "cash app", "robinhood", "venmo",
               "sam's club", "ulta"]

# out of scope: video games and AI / chip makers (product news, not consumer outrage) — never a brand here
NOT_OUR_BRANDS = {"gta", "fortnite", "minecraft", "call of duty", "roblox", "playstation", "xbox", "nintendo", "steam",
                  "epic games", "ubisoft", "valve", "rockstar", "ea sports", "nvidia", "openai", "chatgpt", "windows",
                  "microsoft", "google", "gmail", "android", "pixel", "galaxy", "samsung", "meta", "facebook",
                  "instagram", "whatsapp", "snapchat", "linkedin", "discord", "twitch", "zoom", "canva", "adobe",
                  "dropbox", "coinbase", "fitbit", "kindle", "alexa", "sony"}

# consumer brands that are not US (we are a US index)
NON_US_CONSUMER = ["ryanair", "easyjet", "lufthansa", "british airways", "wizz air", "eurowings", "condor",
                   "deutsche bahn", "kaufland", "dm", "primark", "zalando", "vodafone", "telekom", "o2", "sky", "dhl",
                   "royal mail", "lieferando", "boots", "superdrug", "n26", "revolut", "klarna", "barclays", "hsbc",
                   "lloyds", "natwest", "monzo", "starling", "virgin media", "bt", "ee", "jet2", "tui", "virgin atlantic",
                   "emirates", "qatar airways", "air canada", "westjet", "rogers", "asos", "boohoo", "trainline",
                   "argos", "currys", "john lewis", "h&m", "zara", "uniqlo", "ikea", "puma", "volkswagen", "vw",
                   "bmw", "jaguar", "lidl", "vinted", "depop", "gymshark", "lush"]

# sector keywords (brand names + typical words), for engine/everyday.sector_of
WIDE_SECTORS = {
    "retail": ["amazon", "home depot", "lowe's", "best buy", "macy's", "kohl's", "dollar general", "dollar tree", "cvs",
               "walgreens", "ulta", "etsy", "ebay", "wayfair", "temu", "shein", "chewy", "retailer", "retailers",
               "self-checkout", "self checkout", "return policy", "store closures", "sam's club"],
    "fashion & beauty": ["nike", "lululemon", "under armour", "crocs", "levi's", "ralph lauren", "gap", "old navy",
                         "abercrombie", "american eagle", "victoria's secret", "bath & body works", "estee lauder",
                         "e.l.f.", "sephora", "vans", "the north face", "adidas", "skims", "stanley", "clothing", "fashion",
                         "makeup", "beauty", "sneakers", "apparel"],
    "travel": ["delta air lines", "united airlines", "american airlines", "southwest", "jetblue", "alaska airlines",
               "frontier airlines", "spirit airlines", "marriott", "hilton", "hyatt", "airbnb", "expedia", "uber", "lyft",
               "carnival", "royal caribbean", "norwegian cruise", "hertz", "airline", "airlines", "passengers", "flight",
               "flights", "cruise", "hotel", "resort fee", "baggage"],
    "entertainment & streaming": ["disney", "netflix", "hbo", "warner bros", "spotify", "ticketmaster", "live nation",
                                  "roku", "peacock", "hulu", "paramount", "streaming", "subscribers", "concert", "tickets"],
    "cable & phones": ["apple", "iphone", "verizon", "at&t", "t-mobile", "comcast", "xfinity", "spectrum", "peloton",
                       "duolingo", "tinder", "phone", "cable", "internet", "outage", "wireless", "app"],
    "cars": ["tesla", "ford", "general motors", "chevrolet", "jeep", "toyota", "honda", "harley-davidson", "carvana",
             "car", "cars", "drivers", "dealership", "recall", "electric vehicle", "ev"],
    "banks & payments": ["wells fargo", "chase", "bank of america", "capital one", "citibank", "paypal", "venmo",
                         "cash app", "robinhood", "american express", "bank", "overdraft", "credit card", "account holders"],
}


def _gn(q):
    from engine import narrative_index as ni
    return (ni._gn(q, "US"), "US")


# Google News searches phrased the way people complain, one or two per sector
WIDE_FEEDS = [
    _gn("airline+passengers+furious+OR+outrage+OR+stranded+OR+%22new+fee%22"),
    _gn("cruise+OR+hotel+guests+furious+OR+sick+OR+%22resort+fee%22+OR+backlash"),
    _gn("streaming+%22price+hike%22+OR+%22password+sharing%22+subscribers+furious+OR+cancel"),
    _gn("ticketmaster+OR+%22concert+tickets%22+fans+furious+OR+%22hidden+fees%22+OR+lawsuit"),
    _gn("retailer+customers+furious+OR+backlash+OR+boycott+OR+%22return+policy%22"),
    _gn("%22self-checkout%22+OR+%22membership+fee%22+OR+%22store+closures%22+shoppers+angry"),
    _gn("brand+boycott+customers+OR+shoppers+backlash+apparel+OR+fashion+OR+beauty"),
    _gn("phone+OR+internet+OR+cable+customers+furious+OR+%22price+increase%22+OR+outage+verizon+OR+comcast+OR+%22at%26t%22+OR+t-mobile"),
    _gn("tesla+OR+ford+OR+%22general+motors%22+owners+furious+OR+boycott+OR+lawsuit+OR+backlash"),
    _gn("bank+customers+furious+OR+outage+OR+%22hidden+fees%22+OR+overdraft+wells+fargo+OR+chase+OR+%22bank+of+america%22"),
    _gn("%22class+action%22+OR+lawsuit+customers+misleading+brand+company+consumers"),
    _gn("%22customers+slam%22+OR+%22customers+furious%22+OR+%22customers+outraged%22+company"),
]


def wide_brand(name):
    """The WIDE_BRANDS key for a detected brand name, or None."""
    if not name:
        return None
    k = name.lower().replace("’", "'").strip()
    if k in WIDE_ALIASES:
        k = WIDE_ALIASES[k]
    return k if k in WIDE_BRANDS else None
