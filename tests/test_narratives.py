"""Which big narrative a headline belongs to. Run after any change to engine/narratives.py:
    python tests/test_narratives.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.narratives import tag_rules

CASES = [  # (headline, narratives it must have, narratives it must NOT have)
    ("Taco Bell Customers Claim Beloved Nachos Menu Item Has Gotten 'Smaller' After Packaging Change", {"greedflation"}, {"safety"}),
    ("McDonald's raises prices again and fans are furious", {"greedflation"}, set()),
    ("Kroger begins laying off thousands of employees", {"workers"}, {"greedflation"}),
    ("Cup of woe: Starbucks unionized baristas in Manhattan call for boycott", {"workers"}, {"culture"}),
    ("Chick-fil-A workers walk out over pay cuts", {"workers"}, set()),
    ("Taco Bell sued after diner claims she bit into burrito and choked on rubber glove", {"safety"}, {"greedflation"}),
    ("Costco Kirkland Signature whipped cream recalled over possible cleaning chemical", {"safety"}, set()),
    ("Santa Cruz Co.: Capitola Beach Reopens Following E. Coli Outbreak That Sickened 9 Children", {"safety"}, set()),
    ("Mars to remove artificial dyes from M&M's after pressure from MAHA activists", {"ingredients"}, set()),
    ("Starbucks customers slam new service fee on mobile orders", {"fees"}, set()),
    ("Costco members threaten to cancel over membership fee hike", {"fees"}, set()),
    ("Bud Light sales slump as conservative boycott over transgender influencer campaign drags on", {"culture"}, set()),
    ("Starbucks sales fall as boycott over Israel-Gaza stance spreads in US", {"culture"}, set()),
    ("Wendy's tests AI drive-thru ordering, customers complain", {"automation"}, set()),
    ("Opinion | Consider the real ramifications of a Starbucks boycott", set(), {"culture", "workers"}),
]
fails = 0
for t, must, mustnt in CASES:
    got = set(tag_rules(t))
    if not must <= got or got & mustnt:
        fails += 1
        print(f"WRONG  got {sorted(got)}  wanted {sorted(must)} not {sorted(mustnt)}  |  {t}")
print(f"{len(CASES) - fails}/{len(CASES)} correct")
sys.exit(1 if fails else 0)
