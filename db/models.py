from sqlalchemy import Column, Integer, Float, String, BigInteger, DateTime, ForeignKey, Boolean
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
