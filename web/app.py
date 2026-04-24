from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import os, httpx, math, asyncio
from datetime import datetime, timedelta

from db.database import SessionLocal
from db.models import (Contract, ContractSeries, PublishedRate,
                        Position, Order, Trade, Wallet, User, IndexTick)
from engine.orderbook import (place_order, place_secondary_order,
                               get_order_book, get_market_snapshot, get_all_orders)
from engine.execution import get_trades, cancel_order
from engine.index_provider import (get_risk_index, get_index_snapshot,
                                    get_current_published_rate, publish_daily_rate,
                                    get_running_estimate)
from engine.settlement import settle_contract, _auto_launch_series
from engine.wallet import get_wallet
from engine.users import get_or_create_user, cancel_position
from engine.pnl import get_mm_pnl, calc_mark_price, calc_pnl
from engine.constants import ADMIN_ID, MM_USER_ID, KNOWN_USERS
from config import DISCORD_WEBHOOK_URL

app = FastAPI(title="Quantara API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup_event():
    """Auto-initialise DB tables and seed contract series on first boot."""
    try:
        from db.database import engine, Base
        from db.models import ContractSeries, Wallet
        from engine.constants import MM_USER_ID

        # Create all tables (safe to run multiple times — no-op if already exist)
        Base.metadata.create_all(bind=engine)

        # Add new columns to existing tables that may predate them
        try:
            with engine.connect() as conn:
                conn.execute(
                    __import__('sqlalchemy').text(
                        "ALTER TABLE contract_series ADD COLUMN IF NOT EXISTS paused BOOLEAN DEFAULT FALSE"
                    )
                )
                conn.commit()
        except Exception as col_err:
            print(f"⚠️ Column migration note: {col_err}")

        session = SessionLocal()
        try:
            # Seed the 12 contract series if not already present
            SERIES = [
                (1,  100,    60,    20.0, "1h · €100 · >20%"),
                (2,  1000,   60,    20.0, "1h · €1,000 · >20%"),
                (3,  10000,  60,    20.0, "1h · €10,000 · >20%"),
                (4,  100,    1440,  20.0, "24h · €100 · >20%"),
                (5,  1000,   1440,  20.0, "24h · €1,000 · >20%"),
                (6,  10000,  1440,  20.0, "24h · €10,000 · >20%"),
                (7,  100,    4320,  20.0, "3d · €100 · >20%"),
                (8,  1000,   4320,  20.0, "3d · €1,000 · >20%"),
                (9,  10000,  4320,  20.0, "3d · €10,000 · >20%"),
                (10, 100,    10080, 20.0, "7d · €100 · >20%"),
                (11, 1000,   10080, 20.0, "7d · €1,000 · >20%"),
                (12, 10000,  10080, 20.0, "7d · €10,000 · >20%"),
            ]
            for sid, col, exp, thr, lbl in SERIES:
                if not session.query(ContractSeries).filter(ContractSeries.id == sid).first():
                    session.add(ContractSeries(id=sid, collateral=col,
                                               expiry_mins=exp, threshold=thr, label=lbl))

            # Ensure MM wallet exists
            if not session.query(Wallet).filter(Wallet.user_id == MM_USER_ID).first():
                session.add(Wallet(user_id=MM_USER_ID, cash_balance=100000.0, locked_balance=0.0))

            session.commit()
            # Confirm IndexTick table exists (created by create_all above)
            from db.models import IndexTick
            tick_count = session.query(IndexTick).count()
            print(f"✅ DB initialised — series seeded — {tick_count} index ticks in DB")
        finally:
            session.close()
    except Exception as e:
        print(f"⚠️ Startup DB init error: {e}")

    # ── Start Discord bot as background asyncio task ──
    try:
        from config import DISCORD_TOKEN
        if DISCORD_TOKEN:
            from bot.bot import bot
            asyncio.create_task(bot.start(DISCORD_TOKEN))
            print("🤖 Discord bot starting in background...")
        else:
            print("⚠️ DISCORD_TOKEN not set — bot not started")
    except Exception as e:
        print(f"⚠️ Bot startup error: {e}")


# ── Keep-alive endpoint (used by external uptime monitors) ──
@app.api_route("/ping", methods=["GET", "HEAD"])
def ping():
    return {"status": "ok"}


@app.get("/api/debug/series_paused")
def debug_series_paused():
    """Debug endpoint — shows paused state of all series directly from DB."""
    import sqlalchemy as _sa
    session = SessionLocal()
    try:
        rows = session.execute(
            _sa.text("SELECT id, label, paused FROM contract_series ORDER BY id")
        ).fetchall()
        return [{"id": r[0], "label": r[1], "paused": r[2]} for r in rows]
    finally:
        session.close()


# ── Probability distribution for any time window ──
@app.get("/api/buckets")
def api_buckets(hours: float = 1.0):
    """Return index distribution buckets for the requested window (hours).
    Queries the index_ticks DB table so any window up to 7 days works.
    """
    import time as _time
    cutoff = _time.time() - hours * 3600
    session = SessionLocal()
    try:
        rows = session.query(IndexTick).filter(IndexTick.ts >= cutoff).all()
        vals = [r.value for r in rows]
        if not vals:
            # Fall back to current snapshot buckets (in-memory)
            snap = get_index_snapshot()
            return snap.get("buckets", {"0_20":0,"20_40":0,"40_60":0,"60_80":0,"80_100":0})
        total = len(vals)
        def pct(lo, hi):
            return round(sum(1 for v in vals if lo <= v < hi) / total * 100)
        return {
            "0_20":   pct(0,  20),
            "20_40":  pct(20, 40),
            "40_60":  pct(40, 60),
            "60_80":  pct(60, 80),
            "80_100": pct(80, 100),
            "total_ticks": total,
        }
    finally:
        session.close()


# ── Helpers ──
def parse_user_id(raw) -> int:
    try:
        uid = int(str(raw).strip())
        if uid < 0: raise ValueError
        return uid
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid user ID: {raw!r}")

def get_display_name(user_id: int) -> str:
    return KNOWN_USERS.get(user_id, f"User ...{str(user_id)[-4:]}")

async def post_to_discord(message: str):
    if not DISCORD_WEBHOOK_URL: return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=5)
    except Exception as e:
        print(f"⚠️ Webhook: {e}")

def time_factor(hours_remaining: float, volatility: float) -> float:
    # Modest time premium: 7d contract at vol=3 costs ~20% more than spot,
    # not 80% more. Dampening factor 0.25 keeps premiums realistic.
    vol_adj = min(volatility, 8) / 10
    return 1 + vol_adj * math.sqrt(max(hours_remaining, 0) / 24) * 0.25

def series_fair_price(series: ContractSeries, active_contract=None) -> dict:
    snap = get_index_snapshot()
    idx  = snap["index"]
    vol  = snap["volatility"]

    # Use time REMAINING on the active contract if available,
    # otherwise fall back to full series duration (for display when no contract is open)
    if active_contract and active_contract.expires_at:
        hrs_remaining = max(0, (active_contract.expires_at - datetime.utcnow()).total_seconds() / 3600)
    else:
        hrs_remaining = series.expiry_mins / 60  # full duration — no active contract

    tf   = time_factor(hrs_remaining, vol)
    mid  = round(series.collateral * (idx / 100) * tf, 2)
    spread = 0.10
    return {
        "mid":  mid,
        "bid":  round(mid * (1 - spread), 2),
        "ask":  round(mid * (1 + spread), 2),
        "tf":   round(tf, 3),
        "idx":  round(idx, 2),
        "hrs_remaining": round(hrs_remaining, 1),
    }


# ── Request models ──
class OrderRequest(BaseModel):
    user_id: str; contract_id: int; side: str; price: float; quantity: float

class SecondaryOrderRequest(BaseModel):
    user_id: str; contract_id: int; side: str; price: float; quantity: float
    position_id: Optional[str] = None

class SettleRequest(BaseModel):
    result: str; admin_id: str

class CancelPositionRequest(BaseModel):
    user_id: str; short_id: str

class AdminRequest(BaseModel):
    admin_id: str

class LaunchSeriesRequest(BaseModel):
    admin_id: str; series_id: int

class QuoteAllRequest(BaseModel):
    admin_id: str; spread_pct: float = 10.0; quantity: float = 5.0

class QuoteSeriesRequest(BaseModel):
    admin_id: str; series_id: int; bid: float; ask: float; quantity: float


# =========================================================
# INDEX
# =========================================================
@app.get("/api/index")
def api_index():
    return get_index_snapshot()


# =========================================================
# PUBLISHED RATES
# =========================================================
@app.get("/api/published_rates")
def api_published_rates():
    session = SessionLocal()
    try:
        rates = session.query(PublishedRate).order_by(
            PublishedRate.period_end.desc()
        ).limit(30).all()
        return [
            {
                "id":           r.id,
                "rate":         r.rate,
                "period_start": str(r.period_start),
                "period_end":   str(r.period_end),
                "in_the_money": r.rate > 20.0,
            }
            for r in rates
        ]
    finally:
        session.close()

@app.get("/api/published_rates/current")
def api_current_rate():
    pub = get_current_published_rate()
    pub["running_estimate"] = get_running_estimate()
    return pub

@app.post("/api/published_rates/publish")
def api_publish_rate(admin_id: str):
    if parse_user_id(admin_id) != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Admin only")
    return publish_daily_rate()


# =========================================================
# SERIES
# =========================================================
@app.get("/api/series")
def api_series():
    session = SessionLocal()
    try:
        all_series = session.query(ContractSeries).order_by(
            ContractSeries.expiry_mins, ContractSeries.collateral
        ).all()
        result = []
        for s in all_series:
            # Find active contract for this series
            active = session.query(Contract).filter(
                Contract.series_id == s.id,
                Contract.status    == "OPEN"
            ).first()

            price = series_fair_price(s, active)

            # Count live orders for this series
            has_orders = False
            if active:
                order_count = session.query(Order).filter(
                    Order.contract_id == active.id,
                    Order.status      == "OPEN",
                    Order.order_type  == "PRIMARY"
                ).count()
                has_orders = order_count > 0

            result.append({
                "series_id":    s.id,
                "label":        s.label,
                "collateral":   s.collateral,
                "expiry_mins":  s.expiry_mins,
                "threshold":    s.threshold,
                "active_contract_id": active.id if active else None,
                "has_market":   active is not None,
                "has_orders":   has_orders,
                "expires_in":   _expires_in(active) if active else None,
                "expires_at":   str(active.expires_at) if active else None,
                "fair_mid":     price["mid"],
                "paused":       bool(getattr(s, "paused", False)),
                "fair_bid":     price["bid"],
                "fair_ask":     price["ask"],
                "time_factor":  price["tf"],
                "index":        price["idx"],
            })
        return result
    finally:
        session.close()

def _expires_in(contract) -> Optional[str]:
    if not contract or not contract.expires_at:
        return None
    secs = int((contract.expires_at - datetime.utcnow()).total_seconds())
    if secs <= 0: return "Expiring..."
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"

@app.get("/api/series/{series_id}/price")
def api_series_price(series_id: int):
    session = SessionLocal()
    try:
        s = session.query(ContractSeries).filter(ContractSeries.id == series_id).first()
        if not s: raise HTTPException(status_code=404, detail="Series not found")
        active = session.query(Contract).filter(
            Contract.series_id == series_id, Contract.status == "OPEN").first()
        return series_fair_price(s, active)
    finally:
        session.close()

@app.get("/api/series/{series_id}/book")
def api_series_book(series_id: int):
    session = SessionLocal()
    try:
        active = session.query(Contract).filter(
            Contract.series_id == series_id,
            Contract.status    == "OPEN"
        ).first()
        if not active:
            return {"bids": [], "asks": [], "contract_id": None}
        book = get_order_book(active.id)
        book["contract_id"] = active.id
        return book
    finally:
        session.close()

@app.post("/api/series/{series_id}/pause")
async def api_pause_series(series_id: int, req: AdminRequest):
    admin_id = parse_user_id(req.admin_id)
    if admin_id not in (ADMIN_ID, MM_USER_ID):
        raise HTTPException(status_code=403, detail="Admin only")
    session = SessionLocal()
    try:
        s = session.query(ContractSeries).filter(ContractSeries.id == series_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Series not found")
        s.paused = True
        session.commit()
        await post_to_discord(f"⏸ Series **{s.label}** paused — will not auto-launch after settlement")
        return {"series_id": series_id, "paused": True, "label": s.label}
    finally:
        session.close()


@app.post("/api/series/{series_id}/resume")
async def api_resume_series(series_id: int, req: AdminRequest):
    admin_id = parse_user_id(req.admin_id)
    if admin_id not in (ADMIN_ID, MM_USER_ID):
        raise HTTPException(status_code=403, detail="Admin only")
    session = SessionLocal()
    try:
        s = session.query(ContractSeries).filter(ContractSeries.id == series_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Series not found")
        s.paused = False
        session.commit()
        return {"series_id": series_id, "paused": False, "label": s.label}
    finally:
        session.close()


@app.post("/api/series/{series_id}/launch")
async def api_launch_series(series_id: int, req: LaunchSeriesRequest):
    admin_id = parse_user_id(req.admin_id)
    if admin_id != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Admin only")

    session = SessionLocal()
    try:
        # Check not already open
        existing = session.query(Contract).filter(
            Contract.series_id == series_id,
            Contract.status    == "OPEN"
        ).first()
        if existing:
            raise HTTPException(status_code=400,
                detail=f"Series {series_id} already has open contract #{existing.id}")

        # Cancel any stale open orders from the previous (settled) contract for this series
        prev = session.query(Contract).filter(
            Contract.series_id == series_id,
            Contract.status    == "SETTLED"
        ).order_by(Contract.id.desc()).first()
        if prev:
            stale = session.query(Order).filter(
                Order.contract_id == prev.id,
                Order.status      == "OPEN"
            ).all()
            for o in stale:
                o.status = "CANCELLED"
            if stale:
                session.commit()
                print(f"🗑️ Cleared {len(stale)} stale orders from previous contract #{prev.id}")

        series = session.query(ContractSeries).filter(
            ContractSeries.id == series_id
        ).first()
        if not series:
            raise HTTPException(status_code=404, detail="Series not found")

        idx     = get_risk_index()
        premium = round(series.collateral * (idx / 100), 2)

        contract = Contract(
            name                 = series.label,
            collateral           = series.collateral,
            premium              = premium,
            series_id            = series_id,
            settlement_threshold = series.threshold,
            auto_settle          = True,
            expires_at           = datetime.utcnow() + timedelta(minutes=series.expiry_mins),
        )
        session.add(contract)
        session.commit()
        session.refresh(contract)

        await post_to_discord(
            f"🚀 **Series {series.label} launched** — Contract #{contract.id}\n"
            f"Collateral: €{series.collateral:,.0f} | Premium: €{premium:.2f} | "
            f"Settles: {contract.expires_at.strftime('%Y-%m-%d %H:%M')} UTC"
        )
        return {
            "contract_id": contract.id,
            "series_id":   series_id,
            "label":       series.label,
            "premium":     premium,
            "expires_at":  str(contract.expires_at),
        }
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

@app.post("/api/series/launch_all")
async def api_launch_all(req: LaunchSeriesRequest):
    """Launch all 12 series that don't have an active contract."""
    admin_id = parse_user_id(req.admin_id)
    if admin_id != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Admin only")

    session = SessionLocal()
    launched = []
    try:
        all_series = session.query(ContractSeries).all()
        for s in all_series:
            existing = session.query(Contract).filter(
                Contract.series_id == s.id,
                Contract.status    == "OPEN"
            ).first()
            if existing:
                continue
            idx     = get_risk_index()
            premium = round(s.collateral * (idx / 100), 2)
            contract = Contract(
                name=s.label, collateral=s.collateral, premium=premium,
                series_id=s.id, settlement_threshold=s.threshold, auto_settle=True,
                expires_at=datetime.utcnow() + timedelta(minutes=s.expiry_mins),
            )
            session.add(contract)
            session.flush()
            launched.append({"series_id": s.id, "contract_id": contract.id, "label": s.label})
        session.commit()
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

    await post_to_discord(
        f"🚀 **All {len(launched)} series launched by MM**"
    )
    return {"launched": launched}

def _cancel_mm_orders(session, contract_id: int):
    stale = session.query(Order).filter(
        Order.contract_id == contract_id,
        Order.user_id     == MM_USER_ID,
        Order.status      == "OPEN",
        Order.order_type  == "PRIMARY"
    ).all()
    for o in stale:
        o.status = "CANCELLED"
    if stale:
        session.commit()
        print(f"🗑️  Cancelled {len(stale)} stale MM orders on contract #{contract_id}")
    return len(stale)


@app.post("/api/series/quote_all")
async def api_quote_all(req: QuoteAllRequest):
    """Post bid/ask on all active series contracts at once."""
    admin_id = parse_user_id(req.admin_id)
    if admin_id != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Admin only")

    session = SessionLocal()
    quoted = []
    try:
        all_series = session.query(ContractSeries).all()
        snap = get_index_snapshot()
        idx = snap["index"]; vol = snap["volatility"]

        for s in all_series:
            active = session.query(Contract).filter(
                Contract.series_id == s.id,
                Contract.status    == "OPEN"
            ).first()
            if not active:
                continue

            hrs_left = (active.expires_at - datetime.utcnow()).total_seconds() / 3600
            tf   = time_factor(max(hrs_left, 0), vol)
            mid  = s.collateral * (idx / 100) * tf
            spread = req.spread_pct / 100
            bid  = round(mid * (1 - spread), 2)
            ask  = round(mid * (1 + spread), 2)
            qty  = req.quantity

            _cancel_mm_orders(session, active.id)
            place_order(MM_USER_ID, active.id, "BUY",  bid, qty)
            place_order(MM_USER_ID, active.id, "SELL", ask, qty)
            quoted.append({"series_id": s.id, "contract_id": active.id,
                           "bid": bid, "ask": ask})
    finally:
        session.close()

    await post_to_discord(
        f"📡 **MM quoted {len(quoted)} series** at {req.spread_pct:.0f}% spread"
    )
    return {"quoted": quoted}

@app.post("/api/series/{series_id}/quote")
async def api_quote_series(series_id: int, req: QuoteSeriesRequest):
    admin_id = parse_user_id(req.admin_id)
    if admin_id != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Admin only")
    if req.bid >= req.ask:
        raise HTTPException(status_code=400, detail="Bid must be lower than ask")

    session = SessionLocal()
    try:
        active = session.query(Contract).filter(
            Contract.series_id == series_id,
            Contract.status    == "OPEN"
        ).first()
        if not active:
            raise HTTPException(status_code=404, detail="No active contract for this series")
        _cancel_mm_orders(session, active.id)
        r1 = place_order(MM_USER_ID, active.id, "BUY",  req.bid, req.quantity)
        r2 = place_order(MM_USER_ID, active.id, "SELL", req.ask, req.quantity)
        if not isinstance(r1, dict) or not isinstance(r2, dict):
            raise HTTPException(status_code=400, detail=f"{r1} | {r2}")
        return {"bid_order_id": r1["id"], "ask_order_id": r2["id"],
                "contract_id": active.id}
    finally:
        session.close()


# =========================================================
# RISK CALCULATOR
# =========================================================
@app.get("/api/risk_calculator")
def api_risk_calculator(
    insured_value: float = 100000,
    loss_severity: float = 1.0,
    collateral:    float = None,
    spread_pct:    float = 10.0
):
    snap  = get_index_snapshot()
    index = snap["index"]
    vol   = snap["volatility"]
    prob  = index / 100.0
    if collateral is None:
        collateral = round(insured_value * loss_severity, 2)
    expected_loss   = round(insured_value * loss_severity * prob, 2)
    fair_premium    = round(collateral * prob, 2)
    fair_bid        = round(fair_premium * (1 - spread_pct / 100), 2)
    fair_ask        = round(fair_premium * (1 + spread_pct / 100), 2)
    protection_cost_pct = round((fair_ask / insured_value) * 100, 3) if insured_value > 0 else 0
    return {
        "index": round(index, 2), "probability": round(prob * 100, 2),
        "insured_value": insured_value, "loss_severity_pct": round(loss_severity * 100, 1),
        "effective_collateral": collateral, "expected_loss": expected_loss,
        "fair_premium": fair_premium, "fair_bid": fair_bid, "fair_ask": fair_ask,
        "protection_cost_pct": protection_cost_pct,
    }


# =========================================================
# CONTRACTS (legacy + series instances)
# =========================================================
@app.get("/api/contracts")
def api_contracts():
    session = SessionLocal()
    try:
        now = datetime.utcnow()
        contracts = session.query(Contract).order_by(Contract.id.desc()).all()
        result = []
        for c in contracts:
            expires_in = None
            if c.expires_at and c.status == "OPEN":
                secs = int((c.expires_at - now).total_seconds())
                if secs > 0:
                    h, rem = divmod(secs, 3600); m, s = divmod(rem, 60)
                    expires_in = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
                else:
                    expires_in = "Expiring..."
            result.append({
                "id": c.id, "name": c.name, "series_id": c.series_id,
                "premium": c.premium, "collateral": c.collateral,
                "status": c.status, "result": c.result,
                "settlement_threshold": c.settlement_threshold,
                "created_at": str(c.created_at) if c.created_at else None,
                "expires_at": str(c.expires_at) if c.expires_at else None,
                "expires_in": expires_in,
                "settled_at": str(c.settled_at) if c.settled_at else None,
            })
        return result
    finally:
        session.close()

@app.post("/api/contracts/{contract_id}/settle")
async def api_settle(contract_id: int, req: SettleRequest):
    admin_id = parse_user_id(req.admin_id)
    if admin_id != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Admin only")
    result = settle_contract(contract_id, req.result)
    await post_to_discord(
        f"⚖️ **Contract #{contract_id} settled as {req.result.upper()}**"
    )
    return {"message": result}


# =========================================================
# MARKET / ORDER BOOK
# =========================================================
@app.get("/api/market/{contract_id}")
def api_market(contract_id: int):
    return get_market_snapshot(contract_id)

@app.get("/api/marketplace/{contract_id}")
def api_marketplace(contract_id: int, exclude_user_id: Optional[str] = None):
    session = SessionLocal()
    try:
        index = get_risk_index()
        query = session.query(Order).filter(
            Order.contract_id == contract_id,
            Order.side        == "SELL",
            Order.order_type  == "SECONDARY",
            Order.status      == "OPEN"
        )
        if exclude_user_id:
            try:
                uid = int(str(exclude_user_id).strip())
                query = query.filter(Order.user_id != uid)
            except Exception:
                pass
        listings = query.order_by(Order.price.asc()).all()
        result = []
        for o in listings:
            pos = session.query(Position).filter(
                Position.id == o.position_id).first() if o.position_id else None
            fair_value = round(pos.collateral * (index / 100), 2) if pos else None
            result.append({
                "order_id": o.id, "price": o.price,
                "quantity": o.quantity - o.filled,
                "role": pos.role if pos else "UNKNOWN",
                "collateral": pos.collateral if pos else None,
                "fair_value": fair_value,
                "seller_name": get_display_name(o.user_id),
                "seller_id": str(o.user_id),
                "created_at": str(o.created_at),
            })
        return result
    finally:
        session.close()

@app.get("/api/orderbook/{contract_id}")
def api_orderbook(contract_id: int):
    return get_order_book(contract_id)

@app.get("/api/orderbook")
def api_all_orders():
    return get_all_orders()


# =========================================================
# ORDERS
# =========================================================
@app.post("/api/orders")
async def api_place_order(req: OrderRequest):
    user_id = parse_user_id(req.user_id)
    get_or_create_user(user_id)
    result = place_order(user_id, req.contract_id, req.side, req.price, req.quantity)
    if not isinstance(result, dict):
        raise HTTPException(status_code=400, detail=result)
    name = get_display_name(user_id)
    await post_to_discord(
        f"📥 **{name}** {req.side.upper()} Contract #{req.contract_id} "
        f"@ {req.price} x {req.quantity}"
    )
    return {"status": "ok", "order_id": result["id"]}

@app.post("/api/orders/secondary")
async def api_secondary_order(req: SecondaryOrderRequest):
    user_id = parse_user_id(req.user_id)
    get_or_create_user(user_id)
    result = place_secondary_order(
        user_id=user_id, contract_id=req.contract_id,
        side=req.side, price=req.price, quantity=req.quantity,
        position_id=req.position_id
    )
    if not isinstance(result, dict):
        raise HTTPException(status_code=400, detail=result)
    return {"status": "ok", "order_id": result["id"]}

@app.delete("/api/orders/{order_id}")
def api_cancel_order(order_id: int, user_id: str):
    uid = parse_user_id(user_id)
    session = SessionLocal()
    try:
        result = cancel_order(session, uid, order_id)
        return {"status": result}
    finally:
        session.close()

@app.post("/api/positions/cancel")
async def api_cancel_position(req: CancelPositionRequest):
    user_id = parse_user_id(req.user_id)
    result  = cancel_position(user_id, req.short_id)
    return {"message": result}


# =========================================================
# TRADES + SETTLEMENTS
# =========================================================
@app.get("/api/trades")
def api_trades(contract_id: Optional[int] = None, limit: int = 30):
    trades = get_trades(contract_id=contract_id, limit=limit)
    for t in trades:
        t["buyer_name"]  = get_display_name(t["buyer_id"])
        t["seller_name"] = get_display_name(t["seller_id"])
    return trades


@app.get("/api/trades/user/{user_id}")
def api_user_trades(user_id: int, limit: int = 200):
    """Personal trade history for one user — all trades where they were buyer or seller."""
    session = SessionLocal()
    try:
        from db.models import Trade, Contract, ContractSeries
        rows = session.query(Trade, Contract, ContractSeries).join(
            Contract, Trade.contract_id == Contract.id
        ).outerjoin(
            ContractSeries, Contract.series_id == ContractSeries.id
        ).filter(
            (Trade.buyer_id == user_id) | (Trade.seller_id == user_id)
        ).order_by(Trade.created_at.desc()).limit(limit).all()

        result = []
        for trade, contract, series in rows:
            role = "HOLDER" if trade.buyer_id == user_id else "WRITER"
            result.append({
                "trade_id":      trade.id,
                "contract_id":   trade.contract_id,
                "series_label":  series.label if series else f"Contract #{trade.contract_id}",
                "role":          role,
                "price":         trade.price,
                "quantity":      trade.quantity,
                "trade_type":    trade.trade_type,
                "created_at":    str(trade.created_at),
                "contract_result": contract.result,   # YES / NO / None
                "contract_status": contract.status,   # OPEN / SETTLED
                "collateral":    contract.collateral,
            })
        return result
    finally:
        session.close()

@app.get("/api/settlements")
def api_settlements():
    session = SessionLocal()
    try:
        settled = session.query(Contract).filter(
            Contract.status == "SETTLED"
        ).order_by(Contract.settled_at.desc()).all()
        result = []
        for c in settled:
            positions = session.query(Position).filter(Position.contract_id == c.id).all()
            winners, losers = [], []
            for p in positions:
                if c.result == "YES":
                    if p.role == "HOLDER" and p.status == "SETTLED":
                        winners.append({"name": get_display_name(p.user_id),
                                        "role": p.role, "payout": p.collateral * p.quantity})
                    elif p.role == "WRITER":
                        losers.append({"name": get_display_name(p.user_id),
                                       "role": p.role, "loss": p.collateral * p.quantity})
                else:
                    if p.role == "WRITER" and p.status == "SETTLED":
                        winners.append({"name": get_display_name(p.user_id),
                                        "role": p.role, "payout": p.collateral * p.quantity})
                    elif p.role == "HOLDER":
                        losers.append({"name": get_display_name(p.user_id),
                                       "role": p.role, "loss": p.premium * p.quantity})
            result.append({
                "id": c.id, "name": c.name, "result": c.result,
                "series_id": c.series_id,
                "settled_at": str(c.settled_at),
                "winners": winners, "losers": losers
            })
        return result
    finally:
        session.close()


# =========================================================
# POSITIONS + WALLET
# =========================================================
@app.get("/api/positions/{user_id_str}")
def api_positions(user_id_str: str):
    user_id = parse_user_id(user_id_str)
    session = SessionLocal()
    try:
        index     = get_risk_index()
        positions = session.query(Position).filter(
            Position.user_id == user_id,
            Position.status  == "OPEN"
        ).all()
        listed_ids = {}
        if positions:
            pos_ids = [p.id for p in positions]
            listed  = session.query(Order).filter(
                Order.position_id.in_(pos_ids),
                Order.order_type == "SECONDARY",
                Order.side       == "SELL",
                Order.status     == "OPEN"
            ).all()
            listed_ids = {o.position_id: o.price for o in listed}

        # Get series labels for each contract
        contract_ids = list(set(p.contract_id for p in positions))
        contracts_map = {}
        series_map = {}
        if contract_ids:
            for c in session.query(Contract).filter(Contract.id.in_(contract_ids)).all():
                contracts_map[c.id] = c
                if c.series_id:
                    s = session.query(ContractSeries).filter(
                        ContractSeries.id == c.series_id).first()
                    if s:
                        series_map[c.series_id] = s

        from engine.pnl import calc_mark_price as _cmp, calc_pnl as _cpnl
        return [
            {
                "id":            p.id,
                "short_id":      p.id[:8],
                "contract_id":   p.contract_id,
                "series_id":     contracts_map.get(p.contract_id, {}) and
                                 getattr(contracts_map.get(p.contract_id), 'series_id', None),
                "series_label":  series_map.get(
                    getattr(contracts_map.get(p.contract_id), 'series_id', None),
                    type('', (), {'label': contracts_map.get(p.contract_id, type('', (), {'name': f'#{p.contract_id}'})()).name})()
                ).label if contracts_map.get(p.contract_id) and
                    getattr(contracts_map.get(p.contract_id), 'series_id', None) else
                    getattr(contracts_map.get(p.contract_id), 'name', f'#{p.contract_id}'),
                "role":          p.role,
                "quantity":      p.quantity,
                "entry_price":   p.premium,
                "collateral":    p.collateral,
                "locked_total":  p.collateral * p.quantity if p.role == "WRITER" else 0,
                "mark_price":    _cmp(p.collateral, index),
                "pnl":           _cpnl(p.role, p.premium, p.collateral, index, p.quantity),
                "status":        p.status,
                "listed_for_sale": p.id in listed_ids,
                "listed_price":    listed_ids.get(p.id),
            }
            for p in positions
        ]
    finally:
        session.close()

@app.get("/api/wallet/{user_id_str}")
def api_wallet(user_id_str: str):
    user_id = parse_user_id(user_id_str)
    session = SessionLocal()
    try:
        get_or_create_user(user_id)
        wallet = get_wallet(session, user_id)
        return {
            "user_id":        user_id,
            "cash_balance":   wallet.cash_balance,
            "locked_balance": wallet.locked_balance,
            "available":      wallet.cash_balance - wallet.locked_balance
        }
    finally:
        session.close()

@app.get("/api/user/{user_id_str}")
def api_user(user_id_str: str):
    user_id = parse_user_id(user_id_str)
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        return {
            "user_id":  user_id, "exists": user is not None,
            "name":     get_display_name(user_id),
            "is_admin": user_id == ADMIN_ID,
            "is_mm":    user_id == MM_USER_ID
        }
    finally:
        session.close()

@app.get("/api/mm/stats")
def api_mm_stats(admin_id: str):
    uid = parse_user_id(admin_id)
    if uid != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Admin only")
    return get_mm_pnl()

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))
