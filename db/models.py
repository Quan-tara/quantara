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
