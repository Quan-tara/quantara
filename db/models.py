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


class NarrativePoint(Base):
    """Story score over time, for the story page chart."""
    __tablename__ = "narrative_points"

    id    = Column(Integer, primary_key=True, autoincrement=True)
    sid   = Column(String(16), nullable=False, index=True)
    ts    = Column(Float, nullable=False)
    score = Column(Float, nullable=False)
