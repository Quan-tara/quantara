"""Every headline we have argued about. Run after ANY filter change:
    python tests/test_gate.py
A story must pass the gate to be listed or to count in the Food Reputation Index."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.everyday import gate

GOOD = [  # real pressure on a US food/drink brand
    "Cup of woe: Starbucks unionized baristas in Manhattan call for boycott",
    "College students rally in support of Starbucks baristas urging boycott over contract impasse",
    "Students call for Starbucks boycott over contract impasse",
    "Taco Bell customers outraged over smaller portions for the same price",
    "Chipotle customers slam smaller bowls, call for refunds",
    "McDonald's raises prices again and fans are furious",
    "Dunkin' quietly removes oat milk discount, customers fume",
    "Kroger shoppers furious over new self-checkout fees",
    "Trader Joe's customers slam new bag policy, demand return of old totes",
    "Wendy's backtracks on surge pricing after customer backlash",
    "Chick-fil-A workers walk out over pay cuts",
    "Coca-Cola faces boycott calls over new recipe",
    "Costco members threaten to cancel over membership fee hike",
    "Subway franchisees sue over $6.99 footlong deal",
    "Starbucks customers say they're done with the chain after price hike",
    "Starbucks baristas in U.S. and Canada walk out over staffing",
]
BAD = [  # (headline, why it must stay out)
    ('"Babadook Hip": People Online Are Joking About The Starbucks Braised Short Rib Grilled Cheese Sandwich, And These 28 Comments About What The Meat Looks Like Have Me Wheezing So Hard I Turned Into A Cake Pop', "joke"),
    ("Starbucks short rib sandwich gets roasted online", "joke"),
    ("Is The New Starbucks Braised Short Rib Grilled Cheese Good?", "review"),
    ("28 Jokes About What The Meat In Starbucks' New Braised Short Rib Grilled Cheese Looks Like", "joke"),
    ("5 Disadvantages Of Ordering A Sheet Cake From Costco's Bakery", "listicle"),
    ("What, Exactly, Is In McDonald's Creamy Chili McCrispy Strip Dip?", "explainer"),
    ("Burger King Serves Up Magic: The Gathering Meal — Whopper Deck Box Comes With 100 Card Sleeves", "promo"),
    ("Heartbroken father of missing DoorDash driver shares son's last messages before 32-year-old vanished while out making deliveries", "crime"),
    ("Pittsburgh DoorDash driver Zachariah Schuck goes missing during delivery, car found abandoned | Hindustan Times", "crime"),
    ("Mukwonago Taco Bell Co-Worker Assault Case: Alexander Van Wyhe", "crime"),
    ("Sturm Graz vs Red Bull Salzburg Prediction, Betting Tips, Lineups & Odds | 08 Nov 2026", "sport"),
    ("Kroger Field to host Kentucky homecoming game", "venue"),
    ("KFC’s Italian Alps Plans Spark Outrage as Cortina Ski Resort Rejects Restaurant", "not US"),
    ("Not McDonald's: The Fast Food Chain Fish Sandwich Fans Say Puts Seafood Restaurants 'To Shame'", "praise"),
    ("New Gokul chairman puts milk price hike on hold", "India"),
    ("Two supermarkets copy Tesco's 8% price hike on essential grocery", "UK"),
    ("Asda shoppers fume as price of turkey rises £3", "UK"),
    ("Greggs customers furious as sausage roll price hike confirmed", "UK"),
    ("Rückruf von Käse bei Rewe wegen Listerien-Verdacht: Diese Bundesländer sind betroffen", "German"),
    ("Tim Hortons fans in Canada slam new cup lids", "Canada"),
    ("Starbucks Customer Questions $9 Short Rib Grilled Cheese", "no demand"),
    ("Taco Bell reports sales recovery following lettuce-linked outbreak", "no demand"),
    ("Shrinkflation is hitting snack aisles everywhere", "no brand / place"),
    ("Trader Joe's Upcoming Frozen Dessert Is Giving Fans Sweet '90s Nostalgia", "promo"),
    ("Trader Joe's Upcoming Frozen Dessert Is Giving Fans Sweet '90s Nostalgia With the Return of a Classic", "promo"),
    ("McDonald's Snack Wrap is back and fans want it to stay forever", "promo / already back"),
    ("Taco Bell launches new Cantina menu nationwide", "launch"),
    ("The Discontinued Jersey Mike's Sub That Customers Want Back On The Menu", "nostalgia"),
    ("Jersey Mike's customers beg for discontinued sub back", "nostalgia"),
    ("Petition to bring back Taco Bell's Mexican Pizza tops 100,000 signatures", "nostalgia petition"),
    ("McDonald's fans demand the return of the Snack Wrap", "nostalgia"),
    ("Wendy's customers call on chain to change its new logo", "no stakes"),
    ("Starbucks To Close 250 Coffeehouses In U.S. and Canada", "no demand (but it IS about the US)"),
    ("Tesco PLC (TSCO.L) stock price, news, quote and history", "quote page / UK"),
    ("McDonald's Corporation (MCD) Stock Price, News, Quote & History", "quote page"),
    ("Every fast-food chicken sandwich, ranked", "ranking"),
    ("The Internet Is Furious With Wendy’s After the Fast Food Chain Is Seemingly Caught ‘Lying’", "no concrete demand"),
    # 21 Nov: the brand is not the one in trouble
    ("Doritos teens sue school over AI bag mistake", "bystander: school is sued"),
    ("Maryland high school students sue after AI security error, and a bag of Doritos is allegedly at the center of the lawsuit", "bystander"),
    ("Traumatized teens sue school, cops after Doritos bag triggers AI gun alert", "bystander"),
    ("Four Maryland high school students sue after AI mistakes Doritos bag for gun", "bystander"),
    ("Pizza Hut co-founder passes away at age ninety eight", "obituary"),
    ("Is Kroger (KR) Undervalued Or Is Its Cash Flow Already Priced In?", "investor"),
    ("$1000 Invested In Kroger 20 Years Ago Would Be Worth This Much Today", "investor"),
    ("Kroger stock price target raised after earnings beat, analysts say", "investor"),
]

# The INDEX gate is broader: real pressure counts even without a named brand or an explicit demand,
# and culture-war fights about a named food brand count (they are never shown to players).
INDEX_GOOD = [
    "Las Vegas steakhouse chain in crisis as restaurants shut and furious workers say they're owed wages",
    "Bud Light sales slump as conservative boycott over transgender influencer campaign drags on",
    "Starbucks sales fall as boycott over Israel-Gaza stance spreads in US",
    "Cracker Barrel shares slide after backlash over new logo, boycott calls grow",
    "Cup of woe: Starbucks unionized baristas in Manhattan call for boycott",
    "US grocery prices jump again as shoppers cut back",
    "Costco Kirkland Signature whipped cream recalled over possible cleaning chemical",
    "Taco Bell customers outraged over smaller portions for the same price",
    "Taco Bell Customers Claim Beloved Nachos Menu Item Has Gotten 'Smaller' After Packaging Change",
    "Kroger begins laying off thousands of employees",
    "Opinion | Consider the real ramifications of a Starbucks boycott",
    "Kroger shares fall after salmonella recall hits stores",
    "Hershey shares drop as shoppers boycott over price hikes",
    # 21 Nov: attention brands, US places, food deaths are food safety
    "MrBeast's Lunchly kits found with mold, parents furious",
    "Feastables chocolate bars shrink but price stays the same",
    "Boar's Head listeria outbreak: lawsuits pile up after deaths",
    "Woman dies after eating at Chipotle; family blames E. coli",
]
INDEX_BAD = [
    ("5 Disadvantages Of Ordering A Sheet Cake From Costco's Bakery", "listicle"),
    ("What, Exactly, Is In McDonald's Creamy Chili McCrispy Strip Dip?", "explainer"),
    ("Jersey Mike's customers beg for discontinued sub back", "nostalgia"),
    ("New Gokul chairman puts milk price hike on hold", "India"),
    ("Two supermarkets copy Tesco's 8% price hike on essential grocery", "UK"),
    ('"Babadook Hip": People Online Are Joking About The Starbucks Braised Short Rib Grilled Cheese Sandwich', "joke"),
    ("Republicans and Democrats clash over food stamp cuts in Congress", "politics without a brand"),
    ("Shooting at McDonald's in Chicago leaves two injured", "violence"),
    ("Woke Starbucks? Conservative influencer mocks new cup design", "culture war, no stakes"),
    ("Trader Joe's Upcoming Frozen Dessert Is Giving Fans Sweet '90s Nostalgia", "promo"),
    ("Is Kroger (KR) Undervalued Or Is Its Cash Flow Already Priced In?", "investor"),
    ("$1000 Invested In Kroger 20 Years Ago Would Be Worth This Much Today", "investor"),
    ("Millrose Properties, Inc. Securities Fraud Class Action Result of Undisclosed Rental Home Business and 8.5% Stock Decline - Investors may Contact Lewis Kahn, Esq., at Kahn Swick & Foti, LLC", "securities ad"),
    ("Pizza Hut co-founder passes away at age ninety eight", "obituary"),
    ("Four Maryland high school students sue after AI mistakes Doritos bag for gun", "bystander"),
    ("Kroger dividend: is the payout safe after Q3 earnings?", "investor"),
    ("Santa Cruz Co.: Capitola Beach Reopens Following E. Coli Outbreak That Sickened 9 Children", "beach water, not food"),
    ("Man shot dead outside Taco Bell in Houston", "violence"),
    ("Police: man dies after stabbing at Chicago McDonald's", "crime"),
]

fails = 0
for t in GOOD:
    ok, why = gate(t)
    if not ok:
        fails += 1
        print(f"SHOULD PASS  [{why}]  {t}")
for t, label in BAD:
    ok, why = gate(t)
    if ok:
        fails += 1
        print(f"SHOULD BLOCK ({label}, passed on '{why}')  {t}")
from engine.everyday import index_gate
for t in INDEX_GOOD:
    ok, why = index_gate(t)
    if not ok:
        fails += 1
        print(f"INDEX SHOULD COUNT  [{why}]  {t}")
for t, label in INDEX_BAD:
    ok, why = index_gate(t)
    if ok:
        fails += 1
        print(f"INDEX SHOULD SKIP ({label}, counted as '{why}')  {t}")
# culture-war stories count in the index but are never shown to players
# (Cracker Barrel's logo row has no political words, so players may get it: "Will they reverse the logo?")
for t in INDEX_GOOD[1:3]:
    if gate(t)[0]:
        fails += 1
        print(f"PLAYERS SHOULD NOT SEE (politics)  {t}")
total = len(GOOD) + len(BAD) + len(INDEX_GOOD) + len(INDEX_BAD) + 2
print(f"{total - fails}/{total} correct")
sys.exit(1 if fails else 0)
