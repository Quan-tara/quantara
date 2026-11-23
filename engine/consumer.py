"""
Wider food scope (23 Nov 2026)
==============================
US food & drink alone gave about one real story every 4–5 days. The scope stays FOOD & DRINK, but wider
inside it: more restaurant chains, grocers and warehouse clubs, packaged-food and drink makers, beer and
spirits, food delivery — with news searches for each, so more food stories are found.
(An earlier version of this file widened to other industries — airlines, banks, retail; that was a
misunderstanding and is reverted.)

brand → (sector, Wikipedia article, share ticker or None). Sectors are the food sectors of engine/everyday.
"""

R, S, G = "fast food & restaurants", "snacks & drinks", "groceries & prices"

WIDE_BRANDS = {
    # restaurant chains
    "cheesecake factory": (R, "The_Cheesecake_Factory", "CAKE"), "bj's restaurant": (R, "BJ's_Restaurants", "BJRI"),
    "outback steakhouse": (R, "Outback_Steakhouse", "BLMN"), "red robin": (R, "Red_Robin", "RRGB"),
    "portillo's": (R, "Portillo's_Restaurants", "PTLO"), "cava": (R, "Cava_(restaurant)", "CAVA"),
    "kura sushi": (R, "Kura_Sushi", "KRUS"), "first watch": (R, "First_Watch_(restaurant)", "FWRG"),
    "noodles & company": (R, "Noodles_&_Company", "NDLS"), "el pollo loco": (R, "El_Pollo_Loco", "LOCO"),
    "dave & buster's": (R, "Dave_&_Buster's", "PLAY"), "steak 'n shake": (R, "Steak_n_Shake", "BH"),
    "tim hortons": (R, "Tim_Hortons", "QSR"), "firehouse subs": (R, "Firehouse_Subs", "QSR"),
    "buffalo wild wings": (R, "Buffalo_Wild_Wings", None), "red lobster": (R, "Red_Lobster", None),
    "whataburger": (R, "Whataburger", None), "culver's": (R, "Culver's", None), "panda express": (R, "Panda_Express", None),
    "little caesars": (R, "Little_Caesars", None), "jimmy john's": (R, "Jimmy_John's", None),
    "waffle house": (R, "Waffle_House", None), "golden corral": (R, "Golden_Corral", None),
    "bojangles": (R, "Bojangles", None), "zaxby's": (R, "Zaxby's", None), "dave's hot chicken": (R, "Dave's_Hot_Chicken", None),
    "long john silver's": (R, "Long_John_Silver's", None), "hooters": (R, "Hooters", None),
    "tgi fridays": (R, "TGI_Fridays", None), "chuck e. cheese": (R, "Chuck_E._Cheese", None),
    "uber eats": (R, "Uber_Eats", "UBER"), "grubhub": (R, "Grubhub", None),
    # grocers and warehouse clubs
    "sprouts farmers market": (G, "Sprouts_Farmers_Market", "SFM"), "bj's wholesale": (G, "BJ's_Wholesale_Club", "BJ"),
    "grocery outlet": (G, "Grocery_Outlet", "GO"), "sam's club": (G, "Sam's_Club", "WMT"),
    "dollar general": (G, "Dollar_General", "DG"), "dollar tree": (G, "Dollar_Tree", "DLTR"),
    "safeway": (G, "Safeway_Inc.", "ACI"), "publix": (G, "Publix", None), "h-e-b": (G, "H-E-B", None),
    "wegmans": (G, "Wegmans", None), "food lion": (G, "Food_Lion", None),
    # packaged food
    "general mills": (S, "General_Mills", "GIS"), "cheerios": (S, "Cheerios", "GIS"),
    "campbell's": (S, "Campbell_Soup_Company", "CPB"), "goldfish crackers": (S, "Goldfish_(cracker)", "CPB"),
    "conagra": (S, "Conagra_Brands", "CAG"), "smucker's": (S, "The_J.M._Smucker_Company", "SJM"),
    "uncrustables": (S, "Uncrustables", "SJM"), "hostess": (S, "Hostess_Brands", "SJM"),
    "hormel": (S, "Hormel", "HRL"), "tyson foods": (S, "Tyson_Foods", "TSN"), "mccormick": (S, "McCormick_&_Company", "MKC"),
    "lunchables": (S, "Lunchables", "KHC"), "oscar mayer": (S, "Oscar_Mayer", "KHC"),
    "frito-lay": (S, "Frito-Lay", "PEP"), "doritos": (S, "Doritos", "PEP"), "gatorade": (S, "Gatorade", "PEP"),
    "lay's": (S, "Lay's", "PEP"), "quaker oats": (S, "Quaker_Oats_Company", "PEP"),
    "vital farms": (S, "Vital_Farms", "VITL"), "cal-maine": (S, "Cal-Maine_Foods", "CALM"),
    "lamb weston": (S, "Lamb_Weston", "LW"), "oatly": (S, "Oatly", "OTLY"), "post holdings": (S, "Post_Holdings", "POST"),
    "boar's head": (S, "Boar's_Head_Provision_Company", None), "chobani": (S, "Chobani", None),
    # drinks, beer and spirits
    "keurig dr pepper": (S, "Keurig_Dr_Pepper", "KDP"), "dr pepper": (S, "Dr_Pepper", "KDP"),
    "celsius energy": (S, "Celsius_Holdings", "CELH"), "molson coors": (S, "Molson_Coors_Beverage_Company", "TAP"),
    "coors": (S, "Coors_Brewing_Company", "TAP"), "miller lite": (S, "Miller_Lite", "TAP"),
    "modelo": (S, "Modelo_Especial", "STZ"), "corona": (S, "Corona_(beer)", "STZ"),
    "sam adams": (S, "Samuel_Adams_(beer)", "SAM"), "jack daniel's": (S, "Jack_Daniel's", "BF-B"),
    "guinness": (S, "Guinness", "DEO"), "white claw": (S, "White_Claw_Hard_Seltzer", None),
    "fairlife": (S, "Fairlife", "KO"), "liquid death": (S, "Liquid_Death", None),
}
WIDE_ALIASES = {"bj's": None, "samuel adams": "sam adams", "smuckers": "smucker's", "campbells": "campbell's",
                "lays": "lay's", "frito lay": "frito-lay", "heb": "h-e-b", "buffalo wild wing": "buffalo wild wings"}

# names the food lists lacked, for brand detection
EXTRA_NAMES = [b for b in WIDE_BRANDS] + ["samuel adams"]

# out of scope: video games and AI / chip makers that sometimes share a headline with a food brand
NOT_OUR_BRANDS = {"gta", "fortnite", "minecraft", "call of duty", "roblox", "playstation", "xbox", "nintendo", "steam",
                  "epic games", "ubisoft", "valve", "rockstar", "ea sports", "nvidia", "openai", "chatgpt"}

# consumer brands that are not US (we are a US index)
NON_US_CONSUMER = ["ryanair", "easyjet", "lufthansa", "british airways", "wizz air", "eurowings", "condor",
                   "deutsche bahn", "kaufland", "dm", "primark", "zalando", "vodafone", "telekom", "o2", "sky", "dhl",
                   "royal mail", "lieferando", "boots", "superdrug", "n26", "revolut", "barclays", "hsbc",
                   "lloyds", "natwest", "monzo", "starling", "virgin media", "bt", "ee", "jet2", "tui", "virgin atlantic",
                   "emirates", "qatar airways", "air canada", "westjet", "rogers", "asos", "boohoo", "trainline",
                   "argos", "currys", "john lewis", "lidl"]

WIDE_SECTORS = {}          # food only: the existing food sectors in engine/everyday cover everything


def _gn(q):
    from engine import narrative_index as ni
    return (ni._gn(q, "US"), "US")


# more food & drink news searches, phrased the way people complain
WIDE_FEEDS = [
    _gn("restaurant+chain+%22customers+furious%22+OR+%22customers+slam%22+OR+boycott+OR+%22price+hike%22"),
    _gn("grocery+store+shoppers+furious+OR+overcharged+OR+%22pricing+errors%22+OR+%22price+gouging%22"),
    _gn("%22menu+prices%22+OR+%22value+menu%22+OR+%22fast+food+prices%22+customers+backlash+OR+angry"),
    _gn("fast+food+OR+restaurant+workers+strike+OR+union+OR+layoffs+OR+%22closing+locations%22"),
    _gn("%22food+delivery%22+OR+doordash+OR+instacart+OR+%22uber+eats%22+customers+OR+drivers+furious+OR+fees"),
    _gn("beer+OR+liquor+OR+spirits+brand+backlash+OR+boycott+OR+lawsuit"),
    _gn("soda+OR+%22energy+drink%22+OR+snack+brand+lawsuit+OR+%22class+action%22+OR+backlash"),
    _gn("food+company+%22misleading%22+OR+%22false+advertising%22+OR+%22class+action%22+consumers"),
    _gn("coffee+chain+customers+furious+OR+backlash+OR+%22price+increase%22"),
    _gn("restaurant+chain+closes+OR+bankruptcy+OR+%22shutting+down%22+customers"),
]


def wide_brand(name):
    """The WIDE_BRANDS key for a detected brand name, or None."""
    if not name:
        return None
    k = name.lower().replace("’", "'").strip()
    if k in WIDE_ALIASES:
        k = WIDE_ALIASES[k]
    return k if k in WIDE_BRANDS else None
