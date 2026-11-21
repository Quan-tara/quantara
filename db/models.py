from sqlalchemy import Column, Integer, Float, String, BigInteger, DateTime, ForeignKey, Boolean, Text
from db.database import Base
from datetime import datetime
import uuid


class User(Base):
    __tablename__ = "users"
    id                = Column(BigInteger, primary_key=True)
    balance           = Column(Float, default=10000.0)
    locked_collateral = Column(Float, default=0.0)


class ContractSeries(Base):
    __tablename__ = "contract_series"
    id           = Column(Integer, primary_key=True)
    collateral   = Column(Float, nullable=False)
    expiry_mins  = Column(Integer, nullable=False)
    threshold    = Column(Float, default=20.0)
    label        = Column(String, nullable=False)
    underlying   = Column(String(16), default="DP")   # "DP" Delivery Pulse | "FSNI"


class Contract(Base):
    __tablename__ = "contracts"
    id                   = Column(Integer, primary_key=True, autoincrement=True)
    name                 = Column(String, nullable=False)
    collateral           = Column(Float, nullable=False)
    premium              = Column(Float, nullable=False)
    status               = Column(String, default="OPEN")
    result               = Column(String, nullable=True)
    series_id            = Column(Integer, ForeignKey("contract_series.id"), nullable=True)
    settlement_threshold = Column(Float, default=20.0)
    auto_settle          = Column(Boolean, default=True)
    created_at           = Column(DateTime, default=datetime.utcnow)
    expires_at           = Column(DateTime, nullable=False)
    settled_at           = Column(DateTime, nullable=True)
    underlying           = Column(String(16), default="DP")   # copied from its series
    settlement_rate      = Column(Float, nullable=True)       # index value it settled on


class PublishedRate(Base):
    __tablename__ = "published_rates"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    rate         = Column(Float, nullable=False)
    period_start = Column(DateTime, nullable=False)
    period_end   = Column(DateTime, nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow)


class Position(Base):
    __tablename__ = "positions"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contract_id = Column(Integer, nullable=False)
    user_id     = Column(BigInteger, nullable=False)
    role        = Column(String, nullable=False)
    quantity    = Column(Float, nullable=False)
    premium     = Column(Float, nullable=False)
    collateral  = Column(Float, nullable=False)
    status      = Column(String, default="OPEN")
    trade_id    = Column(Integer, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_id     = Column(BigInteger, nullable=False)
    contract_id = Column(Integer, nullable=False)
    side        = Column(String, nullable=False)
    price       = Column(Float, nullable=False)
    quantity    = Column(Float, nullable=False)
    filled      = Column(Float, default=0.0)
    order_type  = Column(String, default="PRIMARY")
    position_id = Column(String, nullable=True)
    status      = Column(String, default="OPEN")
    created_at  = Column(DateTime, default=datetime.utcnow)


class Trade(Base):
    __tablename__ = "trades"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    contract_id   = Column(Integer, nullable=False)
    buyer_id      = Column(BigInteger, nullable=False)
    seller_id     = Column(BigInteger, nullable=False)
    price         = Column(Float, nullable=False)
    quantity      = Column(Float, nullable=False)
    trade_type    = Column(String, default="USER_TRADE")
    buy_order_id  = Column(Integer, nullable=True)
    sell_order_id = Column(Integer, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)


class Wallet(Base):
    __tablename__ = "wallets"
    user_id        = Column(BigInteger, primary_key=True)
    cash_balance   = Column(Float, default=10000.0)
    locked_balance = Column(Float, default=0.0)


# =========================================================
# INDEX TICK
# Persists every index reading so history survives restarts.
# Kept for the last 60 minutes (360 ticks at 10s intervals).
# Older rows are pruned automatically by the ticker loop.
# =========================================================
class IndexTick(Base):
    __tablename__ = "index_ticks"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    value           = Column(Float, nullable=False)
    volatility      = Column(Float, nullable=False, default=0.0)
    ts              = Column(Float, nullable=False)  # unix timestamp
    intensity_score = Column(Float, nullable=True, default=0.0)
    diffusion_score = Column(Float, nullable=True, default=0.0)
    headlines       = Column(Text, nullable=True)   # JSON list of top article titles
    created_at      = Column(DateTime, default=datetime.utcnow)


class Narrative(Base):
    """A story that crossed the listing threshold. Listed for 72 h, then closed
    with a final story score (what grow/fade positions will settle on later)."""
    __tablename__ = "narratives"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    sid         = Column(String(16), unique=True, nullable=False, index=True)
    title       = Column(Text, nullable=False)
    url         = Column(Text, nullable=True)
    source      = Column(String(200), nullable=True)
    status      = Column(String(12), nullable=False, default="LISTED")   # LISTED | CLOSED
    first_seen  = Column(Float, nullable=True)     # unix ts the story was first picked up
    listed_at   = Column(Float, nullable=False)
    closes_at   = Column(Float, nullable=False)
    list_score  = Column(Float, nullable=False)    # score when it was listed
    last_score  = Column(Float, nullable=True)
    peak_score  = Column(Float, nullable=True)
    final_score = Column(Float, nullable=True)     # score at close
    closed_at   = Column(Float, nullable=True)
    last_seen   = Column(Float, nullable=True)     # last run the story was still in the news
    snapshot    = Column(Text, nullable=True)      # JSON: outlets, regions, categories, articles…
    arena       = Column(String(16), nullable=True, default="food", index=True)   # food | brands | …


class NarrativePoint(Base):
    """Story score over time, for the story page chart."""
    __tablename__ = "narrative_points"

    id    = Column(Integer, primary_key=True, autoincrement=True)
    sid   = Column(String(16), nullable=False, index=True)
    ts    = Column(Float, nullable=False)
    score = Column(Float, nullable=False)


# ── Story trading (virtual credits) ─────────────────────────────────────────
class StoryAccount(Base):
    """Virtual credits for story trading. Separate from the exchange wallet."""
    __tablename__ = "story_accounts"

    user_id    = Column(BigInteger, primary_key=True)
    credits    = Column(Float, nullable=False, default=10000.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class StoryMarket(Base):
    """One market per listed story. Price = story score + offset; the offset is the
    crowd's lean, moved only by trades."""
    __tablename__ = "story_markets"

    sid        = Column(String(16), primary_key=True)
    crowd_offset = Column(Float, nullable=False, default=0.0)
    status     = Column(String(12), nullable=False, default="OPEN")   # OPEN | SETTLED | REFUNDED
    final      = Column(Float, nullable=True)
    updated_at = Column(Float, nullable=True)


class StoryPosition(Base):
    __tablename__ = "story_positions"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(BigInteger, nullable=False, index=True)
    sid        = Column(String(16), nullable=False, index=True)
    side       = Column(String(4), nullable=False)          # GROW | FADE
    units      = Column(Float, nullable=False, default=0.0)
    cost       = Column(Float, nullable=False, default=0.0)  # credits paid for the units held
    realized   = Column(Float, nullable=False, default=0.0)  # profit from units closed early
    status     = Column(String(10), nullable=False, default="OPEN")  # OPEN | SETTLED | REFUNDED
    payout     = Column(Float, nullable=True)
    opened_at  = Column(Float, nullable=True)
    settled_at = Column(Float, nullable=True)


class StoryTrade(Base):
    __tablename__ = "story_trades"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    sid     = Column(String(16), nullable=False, index=True)
    side    = Column(String(4), nullable=False)       # GROW | FADE
    action  = Column(String(5), nullable=False)       # OPEN | CLOSE
    units   = Column(Float, nullable=False)
    price   = Column(Float, nullable=False)           # average credits per unit
    credits = Column(Float, nullable=False)           # paid (OPEN) or received (CLOSE)
    score   = Column(Float, nullable=True)            # story score at the time
    ts      = Column(Float, nullable=False)


# ── Story comments ──────────────────────────────────────────────────────────
class StoryComment(Base):
    """Discussion on a story page. Never an input to the score or the payout."""
    __tablename__ = "story_comments"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    sid       = Column(String(16), nullable=False, index=True)
    user_id   = Column(BigInteger, nullable=False, index=True)
    parent_id = Column(Integer, nullable=True)          # reply to (one level)
    body      = Column(Text, nullable=False)
    verdict   = Column(String(12), nullable=True)       # REAL | EXAGGERATED | HYPE
    side      = Column(String(4), nullable=True)        # commenter's position when posting
    units     = Column(Float, nullable=True)
    ts        = Column(Float, nullable=False)
    deleted   = Column(Boolean, nullable=False, default=False)
    reports   = Column(Integer, nullable=False, default=0)
    # evidence: which outcome the comment argues for (YES / NO), with an optional source link
    kind      = Column(String(4), nullable=True)
    link      = Column(Text, nullable=True)
    useful    = Column(Integer, nullable=True, default=0)
    called    = Column(String(4), nullable=True)        # WIN | LOSE, set when the story settles
    lead_s    = Column(Float, nullable=True)            # how long before the result it was posted
    bonus     = Column(Float, nullable=True)            # credits for the most useful winning evidence


class StoryCommentVote(Base):
    """'Useful' votes on evidence — one per player per comment, never on your own."""
    __tablename__ = "story_comment_votes"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(Integer, nullable=False, index=True)
    user_id    = Column(BigInteger, nullable=False, index=True)
    ts         = Column(Float, nullable=False)


class StoryCommentReport(Base):
    __tablename__ = "story_comment_reports"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(Integer, nullable=False, index=True)
    user_id    = Column(BigInteger, nullable=False)
    reason     = Column(String(200), nullable=True)
    ts         = Column(Float, nullable=False)


class StoryCommentBlock(Base):
    __tablename__ = "story_comment_blocks"

    user_id = Column(BigInteger, primary_key=True)
    ts      = Column(Float, nullable=False)
    note    = Column(String(200), nullable=True)


class StoryPricePoint(Base):
    """GROW price of a listed story over time (each reading and each trade), for the chart."""
    __tablename__ = "story_price_points"

    id    = Column(Integer, primary_key=True, autoincrement=True)
    sid   = Column(String(16), nullable=False, index=True)
    ts    = Column(Float, nullable=False)
    price = Column(Float, nullable=False)


class ArenaReading(Base):
    """Per-run story list for arenas other than food (food keeps using index_ticks, so the
    FSNI and its history are untouched). Used to keep story ids stable and for score history."""
    __tablename__ = "arena_readings"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    arena       = Column(String(16), nullable=False, index=True)
    ts          = Column(Float, nullable=False, index=True)
    story_index = Column(Text, nullable=False)     # JSON list, same shape as FSNI story_index
    meta        = Column(Text, nullable=True)      # JSON: feeds ok, articles, stories…


class BehaviourSignal(Base):
    """Daily Google Trends check for a listed story: are people searching to act
    (boycott / cancel / alternative)? Test data — not used to settle anything yet."""
    __tablename__ = "behaviour_signals"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    sid     = Column(String(16), nullable=False, index=True)
    ts      = Column(Float, nullable=False, index=True)
    geo     = Column(String(4), nullable=True)
    company = Column(String(120), nullable=True)
    status  = Column(String(12), nullable=False, default="OK")    # OK | ERROR | NO_DATA
    result  = Column(Text, nullable=True)      # JSON: per term {ratio, recent, baseline}, verdict
    series  = Column(Text, nullable=True)      # JSON: daily values per term (for charts later)
    error   = Column(String(300), nullable=True)


class SocialSignal(Base):
    """Social buzz for a story (test): how many recent posts mention it, and how they spread."""
    __tablename__ = "social_signals"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    sid     = Column(String(16), nullable=False, index=True)
    ts      = Column(Float, nullable=False, index=True)
    source  = Column(String(16), nullable=False, default="bluesky")
    query   = Column(String(200), nullable=True)
    posts   = Column(Integer, nullable=True)       # posts in the last 24 h
    likes   = Column(Integer, nullable=True)
    reposts = Column(Integer, nullable=True)
    replies = Column(Integer, nullable=True)
    status  = Column(String(12), nullable=False, default="OK")   # OK | ERROR
    error   = Column(String(300), nullable=True)
    sample  = Column(Text, nullable=True)         # JSON: most-liked posts (moderators only)


class BacktestResult(Base):
    """One past case re-measured by the back-test: what our signals would have shown
    around the event, next to the company's share price (research only)."""
    __tablename__ = "backtest_results"

    id     = Column(Integer, primary_key=True, autoincrement=True)
    run_ts = Column(Float, nullable=False, index=True)
    key    = Column(String(40), nullable=False, index=True)
    status = Column(String(12), nullable=False, default="OK")      # OK | PARTIAL | ERROR
    result = Column(Text, nullable=True)       # JSON: summary + daily series
    error  = Column(String(500), nullable=True)


class BrandBuzz(Base):
    """Discovery: how many Bluesky posts mention a food brand in the last 3 hours, and
    whether that is a spike against the brand's own normal level."""
    __tablename__ = "brand_buzz"

    id       = Column(Integer, primary_key=True, autoincrement=True)
    brand    = Column(String(60), nullable=False, index=True)
    ts       = Column(Float, nullable=False, index=True)
    posts    = Column(Integer, nullable=True)
    baseline = Column(Float, nullable=True)        # median of the brand's earlier readings (7 days)
    spike    = Column(Boolean, nullable=False, default=False)
    words    = Column(Text, nullable=True)         # JSON: what the posts are about (top words)
    top_post = Column(Text, nullable=True)         # JSON: most-liked post
    complaints = Column(Integer, nullable=True)    # posts that complain (prices, sick, boycott, rude staff…)
    status   = Column(String(12), nullable=False, default="OK")
    error    = Column(String(300), nullable=True)


class ReputationTick(Base):
    """Food Reputation Index reading (every collector run): safety part (the FSNI) + social part
    (food & drink stories people talk about), plus the outlook (where it is likely heading)."""
    __tablename__ = "reputation_ticks"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    ts      = Column(Float, nullable=False, index=True)
    value   = Column(Float, nullable=False)
    safety  = Column(Float, nullable=True)
    social  = Column(Float, nullable=True)
    normal  = Column(Float, nullable=True)        # median of the last 7 days (None while warming up)
    outlook = Column(Float, nullable=True)        # 0–100
    label   = Column(String(24), nullable=True)   # calm | watch | pressure building
    parts   = Column(Text, nullable=True)         # JSON: outlook parts
    top     = Column(Text, nullable=True)         # JSON: stories driving the social part


class ProtectionPosition(Base):
    """Demo protection bought by a company: pays if the Food Reputation Index averages above
    the strike over the cover period (10% of the cover per point above, up to 100%)."""
    __tablename__ = "protection_positions"

    id       = Column(Integer, primary_key=True, autoincrement=True)
    user_id  = Column(BigInteger, nullable=False, index=True)
    sector   = Column(String(40), nullable=True)
    brand    = Column(String(60), nullable=True)
    notional = Column(Float, nullable=False)
    strike   = Column(Float, nullable=False)
    start_ts = Column(Float, nullable=False)
    end_ts   = Column(Float, nullable=False, index=True)
    premium  = Column(Float, nullable=False)
    status   = Column(String(12), nullable=False, default="OPEN")   # OPEN | SETTLED
    average  = Column(Float, nullable=True)
    payout   = Column(Float, nullable=True)
    settled_at = Column(Float, nullable=True)


class StoryIntent(Base):
    """'Would you still buy from <brand>?' — one answer per player per story (can be changed).
    The share of NO answers is the platform's direct measure of whether talk turns into lost purchases."""
    __tablename__ = "story_intents"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    sid       = Column(String(16), nullable=False, index=True)
    user_id   = Column(BigInteger, nullable=False, index=True)
    still_buy = Column(Boolean, nullable=False)
    company   = Column(String(60), nullable=True, index=True)
    ts        = Column(Float, nullable=False)


class SectorTick(Base):
    """Heat (0–100, drama) and stock price of one food sector at one collector run.
    The price falls when drama rises: 100 = normal, lower = trouble."""
    __tablename__ = "sector_ticks"

    id     = Column(Integer, primary_key=True, autoincrement=True)
    ts     = Column(Float, nullable=False, index=True)
    sector = Column(String(40), nullable=False, index=True)
    heat   = Column(Float, nullable=False)
    normal = Column(Float, nullable=True)
    price  = Column(Float, nullable=False)
    top    = Column(Text, nullable=True)          # JSON: stories driving it


class SectorPosition(Base):
    """A player's holding in one sector stock (virtual credits). LONG = expects calm,
    SHORT = expects trouble. Collateral = shares × entry price, paid when opening."""
    __tablename__ = "sector_positions"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    user_id   = Column(BigInteger, nullable=False, index=True)
    sector    = Column(String(40), nullable=False, index=True)
    side      = Column(String(5), nullable=False)       # LONG | SHORT
    shares    = Column(Float, nullable=False, default=0.0)
    cost      = Column(Float, nullable=False, default=0.0)   # credits paid for the shares held
    realized  = Column(Float, nullable=False, default=0.0)
    status    = Column(String(8), nullable=False, default="OPEN")
    opened_at = Column(Float, nullable=True)


class SectorTrade(Base):
    __tablename__ = "sector_trades"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    sector  = Column(String(40), nullable=False, index=True)
    action  = Column(String(5), nullable=False)      # BUY | SELL
    shares  = Column(Float, nullable=False)
    price   = Column(Float, nullable=False)
    credits = Column(Float, nullable=False)
    ts      = Column(Float, nullable=False, index=True)


class RealityDaily(Base):
    """What really happened, per brand per day: Wikipedia pageviews and the share's closing price.
    Used to check the index against reality (brand '^GSPC' holds the S&P 500)."""
    __tablename__ = "reality_daily"

    id    = Column(Integer, primary_key=True, autoincrement=True)
    brand = Column(String(40), nullable=False, index=True)
    day   = Column(String(10), nullable=False, index=True)     # YYYY-MM-DD
    views = Column(Integer, nullable=True)                     # English Wikipedia, human readers
    close = Column(Float, nullable=True)                       # adjusted close of the brand's share


class ForwardLog(Base):
    """Every warning, written down when it happens — checked later against what really followed."""
    __tablename__ = "forward_log"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    ts      = Column(Float, nullable=False, index=True)
    kind    = Column(String(16), nullable=False, index=True)   # signal | listed | outlook
    brand   = Column(String(40), nullable=True, index=True)
    sid     = Column(String(16), nullable=True, index=True)
    title   = Column(Text, nullable=True)
    fri     = Column(Float, nullable=True)
    outlook = Column(Float, nullable=True)
    label   = Column(String(24), nullable=True)
    weight  = Column(Float, nullable=True)                     # how much the story pushed the index
    result  = Column(Text, nullable=True)                      # JSON, filled in once enough time has passed
    checked = Column(Float, nullable=True)
    tone    = Column(Float, nullable=True)                     # headline tone -1…+1 at the time (test)
    features = Column(Text, nullable=True)                     # JSON: voices, concentration, platforms at the time (test)


class Spot(Base):
    """A player's early call: 'trouble is brewing at <brand>' — before the news has it.
    Wins automatically if a real story about the brand (one that passes the gate) appears
    within SPOT_DAYS. The spotted brand has one shared card (a Narrative with status SPOTTED)."""
    __tablename__ = "spots"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(BigInteger, nullable=False, index=True)
    brand      = Column(String(40), nullable=False, index=True)      # normalised key, e.g. "chipotle"
    card_sid   = Column(String(16), nullable=False, index=True)
    comment_id = Column(Integer, nullable=True)                      # the reason, posted as evidence on the card
    ts         = Column(Float, nullable=False, index=True)
    status     = Column(String(8), nullable=False, default="OPEN")   # OPEN | WON | EXPIRED
    story_sid  = Column(String(16), nullable=True)                   # the real story that confirmed it
    story_title = Column(Text, nullable=True)
    lead_s     = Column(Float, nullable=True)                        # how long before the news it was spotted
    payout     = Column(Float, nullable=True)
    first      = Column(Boolean, nullable=False, default=False)      # first spotter of this card
    resolved_at = Column(Float, nullable=True)


class ThemeTag(Base):
    """Which big narrative(s) a story headline belongs to (cached so each headline is judged once)."""
    __tablename__ = "theme_tags"

    id     = Column(Integer, primary_key=True, autoincrement=True)
    title  = Column(String(300), nullable=False, index=True)
    themes = Column(Text, nullable=True)                         # JSON list of narrative keys ([] = none)
    source = Column(String(8), nullable=False, default="rules")  # rules | ai
    ts     = Column(Float, nullable=False)


class ThemeTick(Base):
    """Hourly strength of one big narrative across all brands (test phase, recorded only)."""
    __tablename__ = "theme_ticks"

    id       = Column(Integer, primary_key=True, autoincrement=True)
    ts       = Column(Float, nullable=False, index=True)
    theme    = Column(String(24), nullable=False, index=True)
    strength = Column(Float, nullable=False, default=0.0)       # sum of the counting stories' weight
    stories  = Column(Integer, nullable=False, default=0)
    brands   = Column(Text, nullable=True)                       # JSON list
    top      = Column(Text, nullable=True)                       # JSON: [[title, sid, weight], …] strongest first


class LearnedBrand(Base):
    """A brand the code didn't know, named by the AI from headlines or video titles. It becomes
    'active' (used everywhere like a listed brand) once it shows up in 2+ separate stories."""
    __tablename__ = "learned_brands"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(String(60), nullable=False, unique=True, index=True)   # lower case
    shown      = Column(String(60), nullable=True)                             # spelling to show
    hits       = Column(Integer, nullable=False, default=1)                    # separate stories seen
    first_seen = Column(Float, nullable=False)
    last_seen  = Column(Float, nullable=False)
    status     = Column(String(8), nullable=False, default="watch")           # watch | active
    example    = Column(Text, nullable=True)


class BrandAsk(Base):
    """Cache: the AI's answer about which brand a headline is about (asked once per headline)."""
    __tablename__ = "brand_asks"

    id     = Column(Integer, primary_key=True, autoincrement=True)
    title  = Column(String(300), nullable=False, index=True)
    brand  = Column(String(60), nullable=True)
    ts     = Column(Float, nullable=False)


class AttentionItem(Base):
    """Attention layer (recorded only): what pulls people's attention to a food brand before it is
    'pressure' — a viral video, being laughed at, a brand nobody had on the list."""
    __tablename__ = "attention_items"

    id     = Column(Integer, primary_key=True, autoincrement=True)
    ts     = Column(Float, nullable=False, index=True)
    kind   = Column(String(12), nullable=False, index=True)    # video | ridicule | new brand
    brand  = Column(String(60), nullable=True, index=True)
    title  = Column(Text, nullable=True)
    url    = Column(Text, nullable=True)
    size   = Column(Float, nullable=True)                      # views (video) or story score (ridicule)
    source = Column(String(80), nullable=True)                 # channel / outlet
    key    = Column(String(120), nullable=True, index=True)    # de-duplication (video id, story title)


class MethodStart(Base):
    """When the current measuring rules started: the 'normal' level, the forward log's main numbers
    and the narratives count only from here, so old and new rules are never mixed."""
    __tablename__ = "method_starts"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(24), nullable=False, unique=True)
    ts      = Column(Float, nullable=False)


class HeadlineTone(Base):
    """How negative a story headline is, -1 (very negative for the brand) … +1 (positive), scored once by the
    AI and cached. Test phase: recorded on warnings, so the track record can show whether tone matters."""
    __tablename__ = "headline_tones"

    id    = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(300), nullable=False, index=True)
    tone  = Column(Float, nullable=True)
    ts    = Column(Float, nullable=False)


class StoryVoices(Base):
    """Who spreads a story on Bluesky (test): how many different accounts, and how much of the engagement
    the 3 biggest accounts carry. Many independent voices = more likely real and lasting."""
    __tablename__ = "story_voices"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    sid           = Column(String(16), nullable=False, index=True)
    ts            = Column(Float, nullable=False, index=True)
    posts         = Column(Integer, nullable=True)
    authors       = Column(Integer, nullable=True)
    concentration = Column(Float, nullable=True)     # share of engagement from the top 3 accounts, 0…1
    top_handle    = Column(String(120), nullable=True)
    status        = Column(String(12), nullable=False, default="OK")


class VideoComments(Base):
    """What people say under a YouTube video about a brand (test): comments read, how many complain,
    how many joke. One row per video per check."""
    __tablename__ = "video_comments"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    video_id   = Column(String(20), nullable=False, index=True)
    sid        = Column(String(16), nullable=True, index=True)       # the story it belongs to, if any
    brand      = Column(String(60), nullable=True, index=True)
    ts         = Column(Float, nullable=False, index=True)
    title      = Column(Text, nullable=True)
    comments   = Column(Integer, nullable=True)
    complaints = Column(Integer, nullable=True)
    jokes      = Column(Integer, nullable=True)
    sample     = Column(Text, nullable=True)      # JSON: a few complaining comments (moderators only)
    status     = Column(String(12), nullable=False, default="OK")
