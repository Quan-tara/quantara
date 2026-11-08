from fastapi import FastAPI, HTTPException
import hashlib, secrets
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
from engine.index_provider import (get_index_snapshot,
                                    get_current_published_rate, publish_daily_rate,
                                    get_running_estimate)
from engine.narrative_index import get_risk_index, run_collector_loop
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

        # Add new columns to existing tables — each runs independently.
        # (Runs before the collector starts; init_db.py also runs these first.)
        _migrations = [
            "ALTER TABLE contract_series ADD COLUMN IF NOT EXISTS underlying VARCHAR(16) DEFAULT 'DP'",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS underlying VARCHAR(16) DEFAULT 'DP'",
            "ALTER TABLE contract_series ADD COLUMN IF NOT EXISTS paused BOOLEAN DEFAULT FALSE",
            "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS settlement_rate FLOAT",
            "ALTER TABLE index_ticks ADD COLUMN IF NOT EXISTS f_weather FLOAT",
            "ALTER TABLE index_ticks ADD COLUMN IF NOT EXISTS f_traffic FLOAT",
            "ALTER TABLE index_ticks ADD COLUMN IF NOT EXISTS f_driver FLOAT",
            "ALTER TABLE index_ticks ADD COLUMN IF NOT EXISTS f_route FLOAT",
            "ALTER TABLE index_ticks ADD COLUMN IF NOT EXISTS f_volume FLOAT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(64)",
            "ALTER TABLE narratives ADD COLUMN IF NOT EXISTS arena VARCHAR(16) DEFAULT 'food'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(128)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username) WHERE username IS NOT NULL",
        ]
        for _sql in _migrations:
            try:
                with engine.connect() as conn:
                    conn.execute(__import__('sqlalchemy').text(_sql))
                    conn.commit()
                    print(f"✅ Migration: {_sql[:60]}")
            except Exception as _me:
                print(f"⚠️ Migration note: {_me}")

        # Start narrative index collector (runs every 15 min in background)
        run_collector_loop()

        session = SessionLocal()
        try:
            # Seed the 12 contract series if not already present
            SERIES = [
                (1,  100,    60,    20.0, "1h Standard Cover"),
                (2,  1000,   60,    20.0, "1h Plus Cover"),
                (3,  10000,  60,    20.0, "1h Pro Cover"),
                (4,  100,    1440,  20.0, "24h Standard Cover"),
                (5,  1000,   1440,  20.0, "24h Plus Cover"),
                (6,  10000,  1440,  20.0, "24h Pro Cover"),
                (7,  100,    4320,  20.0, "3d Standard Cover"),
                (8,  1000,   4320,  20.0, "3d Plus Cover"),
                (9,  10000,  4320,  20.0, "3d Pro Cover"),
                (10, 100,    10080, 20.0, "7d Standard Cover"),
                (11, 1000,   10080, 20.0, "7d Plus Cover"),
                (12, 10000,  10080, 20.0, "7d Pro Cover"),
            ]
            for sid, col, exp, thr, lbl in SERIES:
                if not session.query(ContractSeries).filter(ContractSeries.id == sid).first():
                    session.add(ContractSeries(id=sid, collateral=col,
                                               expiry_mins=exp, threshold=thr, label=lbl,
                                               underlying="DP"))

            # FSNI pilot series — pay YES if the average FSNI over the window > 30.
            # Not auto-launched: the MM launches and quotes them from the exchange.
            FSNI_SERIES = [
                (101, 100,   10080, 30.0, "FSNI 7d Standard Cover"),
                (102, 1000,  10080, 30.0, "FSNI 7d Plus Cover"),
                (103, 10000, 10080, 30.0, "FSNI 7d Pro Cover"),
                (104, 100,   43200, 30.0, "FSNI 30d Standard Cover"),
                (105, 1000,  43200, 30.0, "FSNI 30d Plus Cover"),
                (106, 10000, 43200, 30.0, "FSNI 30d Pro Cover"),
            ]
            for sid, col, exp, thr, lbl in FSNI_SERIES:
                if not session.query(ContractSeries).filter(ContractSeries.id == sid).first():
                    session.add(ContractSeries(id=sid, collateral=col,
                                               expiry_mins=exp, threshold=thr, label=lbl,
                                               underlying="FSNI"))

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
@app.get("/api/ticks_since")
def api_ticks_since(since_ts: float = 0, minutes_ago: float = 0, limit: int = 360):
    """Return index tick values since a given time — for per-position charts."""
    import time as _time
    now = _time.time()

    if minutes_ago > 0:
        since_ts = now - (minutes_ago * 60)
    elif since_ts <= 0 or since_ts > now:
        since_ts = now - 3600

    session = SessionLocal()
    try:
        # Delivery Pulse rows only — FSNI rows (headlines set) share this table
        rows = session.query(IndexTick).filter(
            IndexTick.ts >= since_ts,
            IndexTick.headlines.is_(None),
        ).order_by(IndexTick.ts.asc()).limit(limit).all()

        # If no rows found, return last N ticks from DB regardless of timestamp
        # (handles case where ticker stopped writing temporarily)
        if not rows:
            rows = session.query(IndexTick).filter(
                IndexTick.headlines.is_(None)
            ).order_by(
                IndexTick.ts.desc()
            ).limit(limit).all()
            rows = list(reversed(rows))

        if not rows:
            return {"values": [], "mean": None, "count": 0}

        values = [round(r.value, 2) for r in rows]
        mean   = round(sum(values) / len(values), 2)
        return {"values": values, "mean": mean, "count": len(values)}
    finally:
        session.close()

@app.get("/api/buckets")
def api_buckets(hours: float = 1.0):
    """Return index distribution buckets for the requested window (hours).
    Queries the index_ticks DB table so any window up to 7 days works.
    """
    import time as _time
    cutoff = _time.time() - hours * 3600
    session = SessionLocal()
    try:
        rows = session.query(IndexTick).filter(
            IndexTick.ts >= cutoff, IndexTick.headlines.is_(None)
        ).all()
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
    if user_id in KNOWN_USERS:
        return KNOWN_USERS[user_id]
    # Check if user has a username in DB
    try:
        _s = SessionLocal()
        row = _s.execute(
            __import__('sqlalchemy').text("SELECT username FROM users WHERE id=:id AND username IS NOT NULL"),
            {"id": user_id}
        ).fetchone()
        _s.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return f"User ...{str(user_id)[-4:]}"

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
    from engine.fsni_pricing import is_fsni, series_price as _fsni_series_price
    if is_fsni(series):
        return _fsni_series_price(series, active_contract)
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
    admin_id: str; series_id: int = 0; underlying: str = "DP"   # underlying used by launch_all

class QuoteAllRequest(BaseModel):
    admin_id: str; spread_pct: float = 10.0; quantity: float = 5.0; underlying: str = "DP"

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
    import sqlalchemy as _sa
    session = SessionLocal()
    try:
        # Get paused states via raw SQL to bypass ORM caching completely
        paused_rows = session.execute(
            _sa.text("SELECT id, COALESCE(paused, FALSE) FROM contract_series")
        ).fetchall()
        paused_map = {row[0]: bool(row[1]) for row in paused_rows}

        session.expire_all()
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

            halted = None
            if not active:
                halted = session.query(Contract).filter(
                    Contract.series_id == s.id, Contract.status == "HALTED").first()
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
                "paused":       paused_map.get(s.id, False),
                "fair_bid":     price["bid"],
                "fair_ask":     price["ask"],
                "time_factor":  price["tf"],
                "index":        price["idx"],
                "last_trade_price": None,  # filled below
                "implied_rate": None,       # filled below
                "underlying":   (getattr(s, "underlying", None) or "DP").upper(),
                "halted_contract_id": halted.id if halted else None,
                "halted_expires_in":  _expires_in(halted) if halted else None,
                "model_prob":   price.get("prob"),          # FSNI only
                "running_avg":  price.get("running_avg"),   # FSNI only
                "book_bid":     None,
                "book_ask":     None,
            })
            # Best bid/ask → market-implied probability (FSNI shows this as the sentiment signal)
            if active and result[-1]["underlying"] == "FSNI":
                book = get_order_book(active.id)
                if book.get("bids"): result[-1]["book_bid"] = book["bids"][0]["price"]
                if book.get("asks"): result[-1]["book_ask"] = book["asks"][0]["price"]
        # Enrich with last traded price per series (one query for all)
        import sqlalchemy as _sa2
        if result:
            contract_ids = [r["active_contract_id"] for r in result if r["active_contract_id"]]
            if contract_ids:
                # Get most recent trade per contract
                last_trades = session.execute(
                    _sa2.text("""
                        SELECT DISTINCT ON (c.series_id)
                            c.series_id, t.price, t.created_at
                        FROM trades t
                        JOIN contracts c ON t.contract_id = c.id
                        WHERE c.series_id IS NOT NULL
                          AND c.status = 'OPEN'   -- live contract only, not a settled predecessor
                        ORDER BY c.series_id, t.created_at DESC
                    """)
                ).fetchall()
                last_trade_map = {row[0]: float(row[1]) for row in last_trades}

                for r in result:
                    lt = last_trade_map.get(r["series_id"])
                    if lt and r["collateral"]:
                        r["last_trade_price"] = round(lt, 2)
                        r["implied_rate"] = round((lt / r["collateral"]) * 100, 1)
                    else:
                        r["last_trade_price"] = None
                        r["implied_rate"] = None

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
    import sqlalchemy as _sa
    session = SessionLocal()
    try:
        # Use raw SQL to bypass any ORM caching issues
        result = session.execute(
            _sa.text("UPDATE contract_series SET paused = TRUE WHERE id = :sid"),
            {"sid": series_id}
        )
        session.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Series not found")
        print(f"⏸ Series {series_id} paused in DB (rows affected: {result.rowcount})")
        # Get label for discord
        row = session.execute(
            _sa.text("SELECT label FROM contract_series WHERE id = :sid"),
            {"sid": series_id}
        ).fetchone()
        label = row[0] if row else f"Series {series_id}"
        await post_to_discord(f"⏸ **{label}** paused — will not auto-launch after settlement")
        return {"series_id": series_id, "paused": True, "label": label}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        print(f"❌ Pause error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/api/series/{series_id}/resume")
async def api_resume_series(series_id: int, req: AdminRequest):
    admin_id = parse_user_id(req.admin_id)
    if admin_id not in (ADMIN_ID, MM_USER_ID):
        raise HTTPException(status_code=403, detail="Admin only")
    import sqlalchemy as _sa
    session = SessionLocal()
    try:
        result = session.execute(
            _sa.text("UPDATE contract_series SET paused = FALSE WHERE id = :sid"),
            {"sid": series_id}
        )
        session.commit()
        print(f"▶️ Series {series_id} resumed in DB (rows affected: {result.rowcount})")
        row = session.execute(
            _sa.text("SELECT label FROM contract_series WHERE id = :sid"),
            {"sid": series_id}
        ).fetchone()
        label = row[0] if row else f"Series {series_id}"
        return {"series_id": series_id, "paused": False, "label": label}
    except Exception as e:
        session.rollback()
        print(f"❌ Resume error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/api/series/{series_id}/stop")
async def api_stop_series(series_id: int, req: AdminRequest):
    """Stop a series now (used for FSNI covers, whose contracts run 7–30 days):
      1. cancel every open order on its running contract (MM and users) — no more trading
      2. pause the series so it does not relaunch
      3. if nobody holds a position: close the contract (CANCELLED)
         otherwise: mark it HALTED — no trading, but it still settles at expiry
         so holders and writers get a fair outcome."""
    admin_id = parse_user_id(req.admin_id)
    if admin_id not in (ADMIN_ID, MM_USER_ID):
        raise HTTPException(status_code=403, detail="Admin only")
    import sqlalchemy as _sa
    session = SessionLocal()
    try:
        series = session.query(ContractSeries).filter(ContractSeries.id == series_id).first()
        if not series:
            raise HTTPException(status_code=404, detail="Series not found")
        session.execute(_sa.text("UPDATE contract_series SET paused = TRUE WHERE id = :sid"), {"sid": series_id})
        active = session.query(Contract).filter(
            Contract.series_id == series_id, Contract.status == "OPEN").first()
        result = {"series_id": series_id, "label": series.label, "paused": True,
                  "contract_id": None, "outcome": "no running contract", "orders_cancelled": 0}
        if active:
            orders = session.query(Order).filter(Order.contract_id == active.id, Order.status == "OPEN").all()
            for o in orders:
                o.status = "CANCELLED"
            open_pos = session.query(Position).filter(
                Position.contract_id == active.id, Position.status == "OPEN").count()
            if open_pos == 0:
                active.status = "CANCELLED"
                active.settled_at = datetime.utcnow()
                outcome = "closed (no positions)"
            else:
                active.status = "HALTED"
                outcome = f"halted — {open_pos} open position(s) settle at expiry"
            result.update(contract_id=active.id, outcome=outcome, orders_cancelled=len(orders))
        session.commit()
        await post_to_discord(f"■ **{series.label}** stopped — {result['outcome']}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
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

        from engine.fsni_pricing import is_fsni, underlying_of
        if is_fsni(series):
            premium = series_fair_price(series, None)["mid"]
        else:
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
            underlying           = underlying_of(series),
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
        from engine.fsni_pricing import underlying_of
        _u = (req.underlying or "DP").upper()
        all_series = [x for x in session.query(ContractSeries).all() if underlying_of(x) == _u]
        for s in all_series:
            existing = session.query(Contract).filter(
                Contract.series_id == s.id,
                Contract.status    == "OPEN"
            ).first()
            if existing:
                continue
            from engine.fsni_pricing import is_fsni, underlying_of
            if is_fsni(s):
                premium = series_fair_price(s, None)["mid"]
            else:
                idx     = get_risk_index()
                premium = round(s.collateral * (idx / 100), 2)
            contract = Contract(
                name=s.label, collateral=s.collateral, premium=premium,
                series_id=s.id, settlement_threshold=s.threshold, auto_settle=True,
                expires_at=datetime.utcnow() + timedelta(minutes=s.expiry_mins),
                underlying=underlying_of(s),
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
        from engine.fsni_pricing import underlying_of
        _u = (req.underlying or "DP").upper()
        all_series = [x for x in session.query(ContractSeries).all() if underlying_of(x) == _u]
        snap = get_index_snapshot()
        idx = snap["index"]; vol = snap["volatility"]

        for s in all_series:
            active = session.query(Contract).filter(
                Contract.series_id == s.id,
                Contract.status    == "OPEN"
            ).first()
            if not active or (active.expires_at and active.expires_at <= datetime.utcnow()):
                continue   # nothing to quote, or expired and waiting to settle

            from engine.fsni_pricing import is_fsni, FSNI_SPREAD
            if is_fsni(s):
                # FSNI: model mid, at least ±25 % wide so traders set the real price
                mid    = series_fair_price(s, active)["mid"]
                spread = max(req.spread_pct / 100, FSNI_SPREAD)
            else:
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
                "underlying": (getattr(c, "underlying", None) or "DP").upper(),
                "premium": c.premium, "collateral": c.collateral,
                "status": c.status, "result": c.result,
                "settlement_threshold": c.settlement_threshold,
                "settlement_rate": round(getattr(c,"settlement_rate",None), 2) if getattr(c,"settlement_rate",None) else None,
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
        from engine.fsni_pricing import is_fsni, contract_mark
        _c = session.query(Contract).filter(Contract.id == contract_id).first()
        _fsni = _c is not None and is_fsni(_c)
        result = []
        for o in listings:
            pos = session.query(Position).filter(
                Position.id == o.position_id).first() if o.position_id else None
            if pos and _fsni:
                fair_value = contract_mark(_c, pos.collateral, session)
            else:
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
                "underlying":    (getattr(contract, "underlying", None) or "DP").upper(),
            })
        return result
    finally:
        session.close()

@app.get("/api/leaderboard")
def api_leaderboard():
    session = SessionLocal()
    try:
        import sqlalchemy as _sa
        wallets = session.execute(
            _sa.text("SELECT user_id, cash_balance, locked_balance FROM wallets WHERE user_id != 0 ORDER BY cash_balance DESC")
        ).fetchall()
        rows = []
        for rank, w in enumerate(wallets, 1):
            uid = w[0]
            cash = float(w[1] or 0)
            locked = float(w[2] or 0)
            total = cash + locked
            name = get_display_name(uid)
            trades = session.execute(
                _sa.text("SELECT COUNT(*) FROM trades WHERE buyer_id = :uid OR seller_id = :uid"),
                {"uid": uid}
            ).scalar() or 0
            wins = session.execute(
                _sa.text(
                    "SELECT COUNT(*) FROM positions p JOIN contracts c ON p.contract_id = c.id "
                    "WHERE p.user_id = :uid AND c.status = 'SETTLED' "
                    "AND ((p.role = 'HOLDER' AND c.result = 'YES') OR (p.role = 'WRITER' AND c.result = 'NO'))"
                ), {"uid": uid}
            ).scalar() or 0
            settled = session.execute(
                _sa.text(
                    "SELECT COUNT(*) FROM positions p JOIN contracts c ON p.contract_id = c.id "
                    "WHERE p.user_id = :uid AND c.status = 'SETTLED'"
                ), {"uid": uid}
            ).scalar() or 0
            win_rate = round((wins / settled * 100) if settled > 0 else 0, 1)
            pnl = total - 100000
            rows.append({
                "rank": rank, "user_id": uid, "name": name,
                "balance": round(total, 2), "pnl": round(pnl, 2),
                "trades": trades, "win_rate": win_rate,
            })
        return rows
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
                "underlying": (getattr(c, "underlying", None) or "DP").upper(),
                "settled_at": str(c.settled_at),
                "settlement_rate": round(getattr(c,"settlement_rate",None), 2) if getattr(c,"settlement_rate",None) else None,
                "settlement_threshold": c.settlement_threshold or 20.0,
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

        from engine.pnl import position_mark_pnl as _pmp
        from engine.fsni_pricing import is_fsni as _is_fsni, contract_state as _cstate
        _marks = {p.id: _pmp(session, p, index) for p in positions}
        _fsni_state = {}
        for cid, c in contracts_map.items():
            if _is_fsni(c):
                _fsni_state[cid] = _cstate(c, session)
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
                "mark_price":    _marks[p.id][0],
                "pnl":           _marks[p.id][1],
                "underlying":    "FSNI" if p.contract_id in _fsni_state else "DP",
                # FSNI: average of FSNI readings since the contract opened (what it settles on)
                "running_avg":   (round(_fsni_state[p.contract_id]["elapsed_mean"], 2)
                                  if p.contract_id in _fsni_state
                                  and _fsni_state[p.contract_id]["elapsed_mean"] is not None else None),
                "status":        p.status,
                "listed_for_sale": p.id in listed_ids,
                "listed_price":    listed_ids.get(p.id),
                "expires_at":      str(contracts_map[p.contract_id].expires_at) if p.contract_id in contracts_map and contracts_map[p.contract_id].expires_at else None,
                "created_at":      str(contracts_map[p.contract_id].created_at) if p.contract_id in contracts_map and contracts_map[p.contract_id].created_at else None,
                "created_at_ts":   __import__('calendar').timegm(contracts_map[p.contract_id].created_at.timetuple()) if p.contract_id in contracts_map and contracts_map[p.contract_id].created_at else None,
                "settlement_threshold": contracts_map[p.contract_id].settlement_threshold if p.contract_id in contracts_map else 20.0,
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

# ── Password helpers ──
def _hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

class RegisterRequest(BaseModel):
    username: str
    password: str

class LoginPwRequest(BaseModel):
    username: str
    password: str

@app.post("/api/register")
def api_register(req: RegisterRequest):
    if len(req.username.strip()) < 2:
        raise HTTPException(status_code=400, detail="Username must be at least 2 characters")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    username = req.username.strip()
    pw_hash  = _hash_pw(req.password)
    session  = SessionLocal()
    try:
        # Check username taken
        existing = session.execute(
            __import__('sqlalchemy').text("SELECT id FROM users WHERE username = :u"),
            {"u": username}
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already taken")
        # Generate a new user_id
        import random
        new_id = random.randint(10**9, 10**12)
        # Create user row directly
        session.execute(
            __import__('sqlalchemy').text(
                "INSERT INTO users (id, balance, locked_collateral, username, password_hash) "
                "VALUES (:id, 100000, 0, :u, :p)"
            ), {"id": new_id, "u": username, "p": pw_hash}
        )
        # Create wallet row directly
        session.execute(
            __import__('sqlalchemy').text(
                "INSERT INTO wallets (user_id, cash_balance, locked_balance) "
                "VALUES (:id, 100000, 0)"
            ), {"id": new_id}
        )
        session.commit()
        return {
            "user_id": new_id, "name": username,
            "is_admin": False, "is_mm": False, "exists": True
        }
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

@app.post("/api/login_pw")
def api_login_pw(req: LoginPwRequest):
    pw_hash = _hash_pw(req.password)
    session = SessionLocal()
    try:
        row = session.execute(
            __import__('sqlalchemy').text(
                "SELECT id, username FROM users WHERE username=:u AND password_hash=:p"
            ), {"u": req.username.strip(), "p": pw_hash}
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        uid = row[0]
        name = row[1]
        return {
            "user_id": uid, "name": name,
            "is_admin": uid == ADMIN_ID, "is_mm": uid == MM_USER_ID,
            "exists": True
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

@app.get("/api/admin/users")
def api_admin_users(admin_id: str):
    uid = parse_user_id(admin_id)
    if uid != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Admin only")
    import sqlalchemy as _sa
    session = SessionLocal()
    try:
        rows = session.execute(_sa.text(
            """SELECT u.id, u.username,
                      w.cash_balance, w.locked_balance,
                      (SELECT COUNT(*) FROM trades t WHERE t.buyer_id=u.id OR t.seller_id=u.id) as trades,
                      (SELECT COUNT(*) FROM positions p WHERE p.user_id=u.id) as positions
               FROM users u
               LEFT JOIN wallets w ON w.user_id = u.id
               WHERE u.id != 0
               ORDER BY w.cash_balance DESC NULLS LAST"""
        )).fetchall()
        result = []
        for r in rows:
            uid2, uname, cash, locked, trades, positions = r
            result.append({
                "user_id":   uid2,
                "name":      uname or get_display_name(uid2),
                "auth":      "username/password" if uname else "discord",
                "balance":   round((cash or 0) + (locked or 0), 2),
                "cash":      round(cash or 0, 2),
                "locked":    round(locked or 0, 2),
                "pnl":       round((cash or 0) + (locked or 0) - 100000, 2),
                "trades":    trades or 0,
                "positions": positions or 0,
            })
        return result
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

@app.get("/narrative")
def serve_narrative():
    return FileResponse(os.path.join(os.path.dirname(__file__), "narrative.html"))

# =========================================================
# FSNI — Narrative Index API
# =========================================================
@app.get("/api/narrative/index")
def api_narrative_index():
    """Latest FSNI tick with intensity, diffusion scores and top headlines."""
    import time as _time
    import json as _json
    import sqlalchemy as _sa
    session = SessionLocal()
    try:
        row = session.execute(
            _sa.text(
                "SELECT value, intensity_score, diffusion_score, ts, headlines "
                "FROM index_ticks WHERE headlines IS NOT NULL "
                "ORDER BY ts DESC LIMIT 1"
            )
        ).fetchone()
        if not row:
            return {"value": 10.0, "intensity_score": 0.0, "diffusion_score": 0.0,
                    "ts": _time.time(), "regime": "CALM", "headlines": [],
                    "components": None, "meta": None}
        v, i, d, ts = float(row[0]), float(row[1] or 0), float(row[2] or 0), float(row[3])
        # headlines column: v2 = {"stories","components","meta"}; v1 = plain list of titles
        headlines, components, meta = [], None, None
        try:
            payload = _json.loads(row[4]) if row[4] else []
            if isinstance(payload, dict):
                headlines  = payload.get("stories", [])
                components = payload.get("components")
                meta       = payload.get("meta")
            elif isinstance(payload, list):
                headlines = payload
        except Exception:
            pass
        regime = ("CRITICAL" if v >= 50 else "ELEVATED" if v >= 30
                  else "WATCH" if v >= 15 else "CALM")
        return {"value": round(v, 2), "intensity_score": round(i, 2),
                "diffusion_score": round(d, 2), "ts": ts, "regime": regime,
                "headlines": headlines, "components": components, "meta": meta}
    finally:
        session.close()

_FSNI_ANALYTICS_CACHE = {}

@app.get("/api/narrative/analytics")
def api_narrative_analytics(hours: float = 24.0):
    """FSNI analytics, mirroring the Delivery Pulse ones:
      buckets      — share of readings in each regime (CALM <15, WATCH 15–30, ELEVATED 30–50, CRITICAL 50+)
      volatility   — std dev of readings in the window (index points)
      vol_history  — rolling std dev over the trailing 8 readings (~2 h)
      moves        — readings that jumped ≥3 points vs the previous one, with the lead story at that moment
      daily        — daily (UTC) average FSNI for the last 14 days vs the 30 payout line
    Only readings made with the baseline-relative scoring are used. Cached 60 s."""
    import time as _time, json as _json, math as _math
    import sqlalchemy as _sa
    from datetime import datetime as _dt, timezone as _tz
    hours = max(1.0, min(float(hours), 168.0))
    key = round(hours, 1)
    hit = _FSNI_ANALYTICS_CACHE.get(key)
    if hit and _time.time() - hit[0] < 60:
        return hit[1]

    now = _time.time()
    since = now - max(hours, 14 * 24) * 3600       # daily averages need 14 days
    session = SessionLocal()
    try:
        rows = session.execute(_sa.text(
            "SELECT id, value, intensity_score, diffusion_score, ts FROM index_ticks "
            "WHERE ts >= :since AND headlines LIKE :pat ORDER BY ts ASC"
        ), {"since": since, "pat": '%"raw":%'}).fetchall()
        all_t = [{"id": r[0], "v": float(r[1]), "i": float(r[2] or 0), "d": float(r[3] or 0), "ts": float(r[4])} for r in rows]
        win = [t for t in all_t if t["ts"] >= now - hours * 3600]
        vals = [t["v"] for t in win]

        def std(xs):
            if len(xs) < 2: return 0.0
            m = sum(xs) / len(xs)
            return _math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))

        n = len(vals)
        b = {"calm": 0, "watch": 0, "elevated": 0, "critical": 0}
        for v in vals:
            b["calm" if v < 15 else "watch" if v < 30 else "elevated" if v < 50 else "critical"] += 1
        buckets = {k: (round(c / n * 100) if n else 0) for k, c in b.items()}

        vol_hist = []
        for k in range(len(win)):
            seg = vals[max(0, k - 7): k + 1]
            if len(seg) >= 3:
                vol_hist.append({"ts": win[k]["ts"], "sigma": round(std(seg), 3)})

        # Moves: jumps of >= 3 points between consecutive readings (< 45 min apart)
        moves = []
        for a, c in zip(win, win[1:]):
            dv = c["v"] - a["v"]
            if abs(dv) >= 3 and c["ts"] - a["ts"] < 2700:
                moves.append({"id": c["id"], "ts": c["ts"], "delta": round(dv, 2), "value": round(c["v"], 2),
                              "d_intensity": round(c["i"] - a["i"], 2), "d_diffusion": round(c["d"] - a["d"], 2)})
        moves = moves[-12:][::-1]
        if moves:
            hl = session.execute(_sa.text("SELECT id, headlines FROM index_ticks WHERE id = ANY(:ids)"),
                                 {"ids": [m["id"] for m in moves]}).fetchall()
            lead = {}
            for rid, h in hl:
                try:
                    st = (_json.loads(h) or {}).get("stories") or []
                    lead[rid] = {"title": st[0].get("title"), "outlets": st[0].get("outlets")} if st else None
                except Exception:
                    lead[rid] = None
            for m in moves:
                m["lead"] = lead.get(m.pop("id"))

        # Daily averages (UTC days), last 14 days incl. today
        days = {}
        for t in all_t:
            d = _dt.fromtimestamp(t["ts"], _tz.utc).strftime("%Y-%m-%d")
            days.setdefault(d, []).append(t["v"])
        today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        daily = [{"date": d, "avg": round(sum(v) / len(v), 2), "n": len(v), "today": d == today}
                 for d, v in sorted(days.items())][-14:]

        out = {
            "hours": hours, "count": n,
            "current": round(vals[-1], 2) if vals else None,
            "min": round(min(vals), 2) if vals else None,
            "max": round(max(vals), 2) if vals else None,
            "mean": round(sum(vals) / n, 2) if n else None,
            "volatility": round(std(vals), 3),
            "vol_history": vol_hist,
            "buckets": buckets,
            "moves": moves,
            "daily": daily,
            "threshold": 30.0,
        }
        _FSNI_ANALYTICS_CACHE[key] = (_time.time(), out)
        return out
    finally:
        session.close()


_FSNI_COMP_CACHE = {}

@app.get("/api/narrative/components_history")
def api_narrative_components_history(hours: float = 24.0):
    """Per-component history for the FSNI factor sparklines.
    Returns {"series": {key: [{"ts", "value"}]}, "latest": {key: {"value", "detail"}}}.
    Values are the stored 0–100 component scores (normal ≈ 20). Cached 60 s."""
    import time as _time, json as _json
    import sqlalchemy as _sa
    hours = max(1.0, min(float(hours), 168.0))
    key = round(hours, 1)
    hit = _FSNI_COMP_CACHE.get(key)
    if hit and _time.time() - hit[0] < 60:
        return hit[1]
    session = SessionLocal()
    try:
        rows = session.execute(_sa.text(
            "SELECT ts, headlines FROM index_ticks WHERE ts >= :since AND headlines LIKE :pat ORDER BY ts ASC"
        ), {"since": _time.time() - hours * 3600, "pat": '%"raw":%'}).fetchall()
        series, latest = {}, {}
        for ts, h in rows:
            try:
                comps = (_json.loads(h) or {}).get("components") or {}
            except Exception:
                continue
            for k, c in comps.items():
                if isinstance(c, dict) and isinstance(c.get("value"), (int, float)):
                    series.setdefault(k, []).append({"ts": float(ts), "value": float(c["value"])})
                    latest[k] = {"value": float(c["value"]), "detail": c.get("detail", "")}
        out = {"hours": hours, "series": series, "latest": latest}
        _FSNI_COMP_CACHE[key] = (_time.time(), out)
        return out
    finally:
        session.close()


@app.get("/api/narrative/stories")
def api_narrative_stories():
    """All scored stories from the latest FSNI reading (up to 40), highest story score first.
    Each: sid, title, score (0–100), delta (change over the last 3 h, null if new), outlets,
    articles, regions, categories, first_seen, ts, url, source."""
    import json as _json
    import sqlalchemy as _sa
    session = SessionLocal()
    try:
        row = session.execute(_sa.text(
            "SELECT ts, headlines FROM index_ticks WHERE headlines LIKE :pat ORDER BY ts DESC LIMIT 1"
        ), {"pat": '%"story_index"%'}).fetchone()
        if not row:
            return {"ts": None, "stories": []}
        idx = (_json.loads(row[1]) or {}).get("story_index") or []
        return {"ts": float(row[0]),
                "stories": [{k: v for k, v in x.items() if k not in ("tokens", "hazards")} for x in idx]}
    finally:
        session.close()


@app.get("/stories")
def serve_stories():
    return FileResponse(os.path.join(os.path.dirname(__file__), "story.html"))


@app.get("/story/{sid}")
def serve_story(sid: str):
    return FileResponse(os.path.join(os.path.dirname(__file__), "story.html"))


def _narr_row(n, with_snap=False):
    import json as _json
    import time as _time
    snap = _json.loads(n.snapshot or "{}")
    d = {"sid": n.sid, "title": n.title, "url": n.url, "source": n.source, "status": n.status,
         "first_seen": n.first_seen, "listed_at": n.listed_at, "closes_at": n.closes_at,
         "closed_at": n.closed_at, "list_score": n.list_score, "score": n.last_score,
         "peak_score": n.peak_score, "final_score": n.final_score, "last_seen": n.last_seen,
         "in_news": bool(n.last_seen and n.last_seen >= _time.time() - 45 * 60),
         "outlets": snap.get("outlets", 0), "articles": len(snap.get("articles", [])),
         "regions": snap.get("regions", []), "categories": snap.get("categories", []),
         "merged_into": snap.get("merged_into"),
         "arena": n.arena or "food", "company": snap.get("company"),
         "sector": snap.get("sector"), "trending": snap.get("trending")}
    if with_snap:
        d["snapshot"] = snap
    return d


def _latest_story_index(session):
    """Latest story list of every arena (food from the FSNI reading, others from arena_readings)."""
    import json as _json
    import sqlalchemy as _sa
    ts, out = None, []
    row = session.execute(_sa.text(
        "SELECT ts, headlines FROM index_ticks WHERE headlines LIKE :pat ORDER BY ts DESC LIMIT 1"
    ), {"pat": '%"story_index"%'}).fetchone()
    if row:
        ts = float(row[0])
        out += [{**x, "arena": x.get("arena") or "food"} for x in (_json.loads(row[1]) or {}).get("story_index") or []]
    try:
        from engine.arenas import all_arenas
        for name in all_arenas():
            r = session.execute(_sa.text(
                "SELECT ts, story_index FROM arena_readings WHERE arena = :a ORDER BY ts DESC LIMIT 1"
            ), {"a": name}).fetchone()
            if r:
                ts = max(ts or 0, float(r[0]))
                out += [{**x, "arena": name} for x in _json.loads(r[1]) or []]
    except Exception as e:
        print(f"⚠️ arena story index: {e}")
    return ts, out


@app.get("/api/narrative/listed")
def api_narrative_listed(days: float = 14.0):
    """Listed stories (open first, then recently closed) with a small score sparkline each."""
    import time as _time
    from db.models import Narrative, NarrativePoint
    from engine.story_listing import LIST_SCORE, LIST_MIN_OUTLETS, LIST_WINDOW_H
    session = SessionLocal()
    try:
        since = _time.time() - days * 86400
        rows = session.query(Narrative).filter(Narrative.listed_at >= since,
                                               Narrative.status.notin_(["MERGED", "DELISTED"])).all()
        rows.sort(key=lambda n: (n.status != "LISTED", -(n.last_score or 0) if n.status == "LISTED" else -(n.closed_at or 0)))
        sids = [n.sid for n in rows]
        pts = {}
        if sids:
            for p in (session.query(NarrativePoint).filter(NarrativePoint.sid.in_(sids))
                      .order_by(NarrativePoint.ts).all()):
                pts.setdefault(p.sid, []).append([p.ts, p.score])
        out = []
        from db.models import StoryMarket
        from engine.story_market import grow_price, trading_state, base_for, bet_of
        mk = {m.sid: m for m in session.query(StoryMarket).filter(StoryMarket.sid.in_(sids or ["-"])).all()}
        import sqlalchemy as _sa
        ccount = dict(session.execute(_sa.text(
            "SELECT sid, count(*) FROM story_comments WHERE NOT deleted GROUP BY sid")).fetchall())
        for n in rows:
            d = _narr_row(n)
            d["spark"] = pts.get(n.sid, [])[-60:]
            m = mk.get(n.sid)
            d["price"] = round(grow_price(base_for(n), m.crowd_offset if m else 0), 2)
            b_ = bet_of(n)
            d["bet"] = {"kind": "line", "line": b_["line"]} if b_ else {"kind": "linear"}
            d["trading_open"] = trading_state(n)[0]
            d["comments"] = ccount.get(n.sid, 0)
            out.append(d)
        from engine.story_listing import ARENA_RULES
        from engine.arenas import ARENA_LABELS
        return {"now": _time.time(), "rules": {"score": LIST_SCORE, "outlets": LIST_MIN_OUTLETS,
                                                "window_h": LIST_WINDOW_H},
                "arenas": {k: {**v, "label": ARENA_LABELS.get(k, k)} for k, v in ARENA_RULES.items()},
                "stories": out}
    finally:
        session.close()


@app.get("/api/narrative/story/{sid}")
def api_narrative_story(sid: str):
    """One story: score history, articles, how it started, related stories.
    Works for listed stories and for any story in the latest reading."""
    import time as _time
    from db.models import Narrative, NarrativePoint
    from engine.story_listing import (LIST_SCORE, LIST_MIN_OUTLETS, LIST_WINDOW_H,
                                      history_from_readings, rules_for)
    sid = (sid or "")[:16]
    session = SessionLocal()
    try:
        now = _time.time()
        n = session.query(Narrative).filter(Narrative.sid == sid).first()
        idx_ts, idx = _latest_story_index(session)
        live = next((x for x in idx if x.get("sid") == sid), None)
        if not n and not live:
            raise HTTPException(status_code=404, detail="Story not found")
        if n:
            d = _narr_row(n, with_snap=True)
            snap = d.pop("snapshot")
            pts = [[p.ts, p.score] for p in session.query(NarrativePoint)
                   .filter(NarrativePoint.sid == sid).order_by(NarrativePoint.ts).all()]
            articles = snap.get("articles", [])
            d["outlet_names"] = snap.get("outlet_names", [])
            tokens, hazards = set(snap.get("tokens", [])), set(snap.get("hazards", []))
        else:
            d = {"sid": sid, "title": live["title"], "url": live.get("url"), "source": live.get("source"),
                 "status": "WATCHING", "first_seen": live.get("first_seen"), "score": live.get("score"),
                 "delta": live.get("delta"), "outlets": live.get("outlets", 0),
                 "articles": live.get("articles", 0), "regions": live.get("regions", []),
                 "categories": live.get("categories", []), "in_news": True,
                 "arena": live.get("arena", "food"), "company": live.get("company"),
                 "sector": live.get("sector"), "trending": live.get("trending"),
                 "needs": {"score": rules_for(live.get("arena")).get("score"),
                           "outlets": rules_for(live.get("arena")).get("outlets")}}
            pts = [[t, sc] for t, sc in history_from_readings(session, sid, now - 72 * 3600, live.get("arena", "food"))]
            articles = [{"title": live["title"], "source": live.get("source"), "url": live.get("url"),
                         "ts": live.get("ts"), "region": (live.get("regions") or [""])[0]}]
            d["outlet_names"] = []
            tokens, hazards = set(live.get("tokens", [])), set(live.get("hazards", []))
        if live and n:
            d["delta"] = live.get("delta")

        # Related: other stories sharing distinctive words or the same hazard
        cands = {}
        for x in idx:
            if x.get("sid") != sid:
                cands[x["sid"]] = {"sid": x["sid"], "title": x["title"], "score": x.get("score"),
                                   "status": "LISTED" if x.get("listed") else "WATCHING",
                                   "tokens": set(x.get("tokens", [])), "hazards": set(x.get("hazards", []))}
        import json as _json
        for m in session.query(Narrative).filter(Narrative.listed_at >= now - 30 * 86400).all():
            if m.sid == sid or m.status in ("MERGED", "DELISTED"):
                continue
            sn = _json.loads(m.snapshot or "{}")
            cands[m.sid] = {"sid": m.sid, "title": m.title, "score": m.final_score if m.status == "CLOSED" else m.last_score,
                            "status": m.status, "tokens": set(sn.get("tokens", [])),
                            "hazards": set(sn.get("hazards", []))}
        rel = []
        for c in cands.values():
            shared = tokens & c["tokens"]
            same_h = bool(hazards & c["hazards"])
            r = len(shared) / max(1, len(tokens | c["tokens"])) + (0.15 if same_h else 0)
            if (len(shared) >= 1 and r >= 0.12) or same_h:
                rel.append({"sid": c["sid"], "title": c["title"], "score": c["score"], "status": c["status"],
                            "shared": sorted(shared)[:5], "same_hazard": same_h, "r": round(r, 3)})
        rel.sort(key=lambda x: x["r"], reverse=True)

        # Score breakdown, as of the latest reading
        try:
            from engine.narrative_index import story_score_parts, score_if_quiet
            at = idx_ts or now
            if n:
                sn = _json.loads(n.snapshot or "{}")
                arts_ts = [a["ts"] for a in sn.get("articles", []) if a.get("ts")]
                si = sn.get("score_inputs")
                if si:                                  # exactly what the last score was computed from
                    src = {k: v for k, v in si.items() if k != "at"}
                    at = si.get("at") or at
                    if n.status != "LISTED":            # closed: frozen at its last reading
                        arts_ts = None
                elif n.status != "LISTED":
                    raise LookupError("no stored inputs for a story closed before this update")
                else:
                    src = {"outlets": sn.get("outlets", 0), "regions": sn.get("regions", []),
                           "severity": sn.get("severity", 0), "ts": sn.get("ts") or at,
                           "recent_12h": sum(1 for t in arts_ts if t >= at - 12 * 3600)}
                    if live and live.get("severity") is not None:      # in this reading: use its numbers
                        src.update({k: live[k] for k in ("outlets", "outlets_eff", "regions", "severity", "recent_12h", "ts") if k in live})
            else:
                arts_ts = None
                src = {k: live.get(k) for k in ("outlets", "outlets_eff", "regions", "severity", "recent_12h", "ts")}
            bd = story_score_parts(src, at)
            _sev_txt = {"brands": "fraud, criminal charges, fines, resignations, boycotts, bankruptcy…",
                        "everyday": "millions affected, boycotts, petitions, viral backlash, U-turns…"}
            if (d.get("arena") or "food") in _sev_txt:
                for p_ in bd["parts"]:
                    if p_["key"] == "severity":
                        p_["detail"] = p_["detail"].split(" — ")[0] + " — " + _sev_txt[d.get("arena")]
            bd["as_of"] = at
            bd["frozen"] = bool(n and n.status != "LISTED")
            bd["complete"] = src.get("severity") is not None
            if n and n.status == "LISTED":
                bd["quiet_at_close"] = score_if_quiet(src, n.closes_at, arts_ts if not (live and live.get("recent_12h") is not None) else None)
                bd["quiet_in_12h"] = score_if_quiet(src, at + 12 * 3600, arts_ts if not (live and live.get("recent_12h") is not None) else None)
            d["breakdown"] = bd
        except LookupError:
            d["breakdown"] = None
        except Exception as e:
            print(f"⚠️ breakdown error {sid}: {e}")
        from db.models import StoryPricePoint
        d["price_points"] = [[p.ts, p.price] for p in session.query(StoryPricePoint)
                             .filter(StoryPricePoint.sid == sid).order_by(StoryPricePoint.ts).all()]
        arts = sorted(articles, key=lambda a: a.get("ts") or 0)
        d["article_count"] = max(len(articles), d.get("articles") or 0) if isinstance(d.get("articles"), int) else len(articles)
        d.update({
            "now": now, "reading_ts": idx_ts, "points": pts,
            "articles": sorted(articles, key=lambda a: a.get("ts") or 0, reverse=True),
            "origin": arts[:3],
            "hazards": sorted(hazards),
            "related": rel[:6],
            "rules": {"score": rules_for(d.get("arena")).get("score"),
                      "outlets": rules_for(d.get("arena")).get("outlets"), "window_h": LIST_WINDOW_H},
        })
        from engine.arenas import ARENA_LABELS
        d["arena_label"] = ARENA_LABELS.get(d.get("arena") or "food", d.get("arena"))
        if n:
            d["delisted"] = _json.loads(n.snapshot or "{}").get("delisted")
            from engine.story_market import bet_of
            b_ = bet_of(n)
            d["bet"] = {"kind": "line", "line": b_["line"]} if b_ else {"kind": "linear"}
        return d



    finally:
        session.close()


from pydantic import BaseModel as _BM
from typing import Optional as _Opt


@app.get("/api/stories/candidates")
def api_story_candidates(user_id: str, arena: str = "everyday", limit: int = 25):
    """Moderators: the strongest stories of the latest reading that are NOT listed — to see
    what the filter finds and tune the listing bar with real examples."""
    import json as _json
    import sqlalchemy as _sa
    from engine.story_comments import is_moderator
    from engine.story_listing import rules_for
    if not is_moderator(parse_user_id(user_id)):
        raise HTTPException(status_code=403, detail="Moderators only")
    session = SessionLocal()
    try:
        r = session.execute(_sa.text(
            "SELECT ts, story_index, meta FROM arena_readings WHERE arena = :a ORDER BY ts DESC LIMIT 1"
        ), {"a": arena[:16]}).fetchone()
        if not r:
            return {"arena": arena, "ts": None, "rules": rules_for(arena), "stories": []}
        idx = _json.loads(r[1]) or []
        rows = [x for x in idx if not x.get("listed")]
        rows.sort(key=lambda x: x.get("score") or 0, reverse=True)
        rules = rules_for(arena)
        out = []
        for x in rows[:max(1, min(limit, 60))]:
            eff = x.get("outlets_eff") if x.get("outlets_eff") is not None else x.get("outlets", 0)
            why = []
            if (x.get("score") or 0) < rules["score"]:
                why.append(f"score {x.get('score', 0):.1f} < {rules['score']:g}")
            if eff < rules["outlets"]:
                why.append(f"{eff:g} outlets < {rules['outlets']}")
            if x.get("list_status"):
                why.append(f"was {x['list_status'].lower()}")
            out.append({"sid": x.get("sid"), "title": x.get("title"), "score": x.get("score"),
                        "outlets": x.get("outlets"), "company": x.get("company"), "sector": x.get("sector"),
                        "trending": x.get("trending"), "regions": x.get("regions"),
                        "why_not": "; ".join(why) or "cap reached or listed earlier"})
        return {"arena": arena, "ts": float(r[0]), "rules": rules, "meta": _json.loads(r[2] or "{}"),
                "stories": out}
    finally:
        session.close()


@app.get("/api/behaviour/{sid}")
def api_behaviour(sid: str):
    """Behaviour checks recorded for one story (test phase)."""
    from engine.behaviour import latest_for
    session = SessionLocal()
    try:
        return {"sid": sid, "checks": latest_for(session, sid[:16])}
    finally:
        session.close()


@app.get("/api/behaviour")
def api_behaviour_all(days: float = 21.0):
    """Latest behaviour check per story over the last weeks — for evaluating the test."""
    import json as _json
    import time as _time
    from db.models import BehaviourSignal, Narrative
    session = SessionLocal()
    try:
        since = _time.time() - days * 86400
        rows = (session.query(BehaviourSignal).filter(BehaviourSignal.ts >= since)
                .order_by(BehaviourSignal.ts.desc()).all())
        latest = {}
        for r in rows:
            latest.setdefault(r.sid, r)
        nar = {n.sid: n for n in session.query(Narrative).filter(Narrative.sid.in_(list(latest) or ["-"])).all()}
        stats = {"OK": 0, "ERROR": 0, "NO_DATA": 0, "acted": 0}
        out = []
        for sid, r in latest.items():
            res = _json.loads(r.result) if r.result else None
            stats[r.status] = stats.get(r.status, 0) + 1
            if res and res.get("acted"):
                stats["acted"] += 1
            n = nar.get(sid)
            out.append({"sid": sid, "title": n.title if n else None, "status": r.status, "ts": r.ts,
                        "geo": r.geo, "company": r.company, "error": r.error, "result": res,
                        "final_score": n.final_score if n else None})
        return {"stats": stats, "stories": out}
    finally:
        session.close()


class DelistRequest(_BM):
    user_id: str
    reason: _Opt[str] = None


@app.post("/api/narrative/story/{sid}/delist")
def api_story_delist(sid: str, req: DelistRequest):
    """Moderators remove a listed story; all positions on it are refunded."""
    from engine.story_listing import delist
    try:
        delist(sid[:16], parse_user_id(req.user_id), req.reason)
        return {"status": "ok"}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (LookupError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# =========================================================
# STORY TRADING — grow / fade with virtual credits (engine/story_market.py)
# =========================================================
from pydantic import BaseModel as _BM
from typing import Optional as _Opt


class StoryTradeRequest(_BM):
    user_id: str
    sid: str
    side: str
    action: str = "OPEN"
    units: int


@app.get("/api/stories/market/{sid}")
def api_story_market(sid: str, user_id: str = None):
    from db.models import Narrative
    from engine.story_market import market_view
    session = SessionLocal()
    try:
        n = session.query(Narrative).filter(Narrative.sid == sid[:16]).first()
        if not n:
            raise HTTPException(status_code=404, detail="Story is not listed")
        uid = parse_user_id(user_id) if user_id else None
        v = market_view(session, n, uid)
        names = {}
        for t in v["trades"]:
            if t["user_id"] not in names:
                try:
                    names[t["user_id"]] = get_display_name(int(t["user_id"]))
                except Exception:
                    names[t["user_id"]] = "trader"
            t["name"] = names[t["user_id"]]
            del t["user_id"]
        return v
    finally:
        session.close()


@app.post("/api/stories/trade")
def api_story_trade(req: StoryTradeRequest):
    from engine.story_market import trade, TradeError
    uid = parse_user_id(req.user_id)
    try:
        return {"status": "ok", **trade(uid, req.sid[:16], req.side, req.action, req.units)}
    except TradeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/stories/account/{user_id_str}")
def api_story_account(user_id_str: str):
    """Credits and all story positions (open first) for one user."""
    from db.models import Narrative, StoryPosition, StoryMarket
    from engine.story_market import account, close_value, base_for
    uid = parse_user_id(user_id_str)
    session = SessionLocal()
    try:
        acc = account(session, uid)
        session.commit()
        rows = session.query(StoryPosition).filter(StoryPosition.user_id == uid).all()
        sids = list({r.sid for r in rows}) or ["-"]
        nar = {n.sid: n for n in session.query(Narrative).filter(Narrative.sid.in_(sids)).all()}
        mk = {m.sid: m for m in session.query(StoryMarket).filter(StoryMarket.sid.in_(sids)).all()}
        out, open_value, pnl_total = [], 0.0, 0.0
        for r in rows:
            if r.status == "CLOSED" and not r.realized:
                continue
            n, m = nar.get(r.sid), mk.get(r.sid)
            value = (close_value(base_for(n) if n else 0, m.crowd_offset if m else 0, r.side, r.units)
                     if r.status == "OPEN" else (r.payout or 0))
            pnl = value - r.cost + r.realized
            if r.status == "OPEN":
                open_value += value
            pnl_total += pnl
            out.append({"sid": r.sid, "title": n.title if n else r.sid, "side": r.side,
                        "units": r.units, "cost": round(r.cost, 2), "status": r.status,
                        "story_status": n.status if n else None,
                        "closes_at": n.closes_at if n else None,
                        "value": round(value, 2), "pnl": round(pnl, 2), "payout": r.payout,
                        "final": n.final_score if n else None, "settled_at": r.settled_at})
        out.sort(key=lambda x: (x["status"] != "OPEN", -(x["settled_at"] or 0)))
        return {"credits": round(acc.credits, 2), "open_value": round(open_value, 2),
                "net_worth": round(acc.credits + open_value, 2), "pnl": round(pnl_total, 2),
                "positions": out}
    finally:
        session.close()


# =========================================================
# STORY COMMENTS (engine/story_comments.py) — never an input to scores or payouts
# =========================================================
class StoryCommentRequest(_BM):
    user_id: str
    body: str
    verdict: _Opt[str] = None
    parent_id: _Opt[int] = None


class StoryCommentActionRequest(_BM):
    user_id: str
    action: str = "report"     # report | delete | restore | block | unblock
    reason: _Opt[str] = None


@app.get("/api/stories/{sid}/comments")
def api_story_comments(sid: str, user_id: str = None):
    from engine.story_comments import thread
    uid = parse_user_id(user_id) if user_id else None
    return thread(sid[:16], uid, get_display_name)


@app.post("/api/stories/{sid}/comments")
def api_story_comment_post(sid: str, req: StoryCommentRequest):
    from engine.story_comments import post, CommentError
    try:
        cid = post(parse_user_id(req.user_id), sid[:16], req.body, req.verdict, req.parent_id)
        return {"status": "ok", "id": cid}
    except CommentError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/stories/comments/{comment_id}/action")
def api_story_comment_action(comment_id: int, req: StoryCommentActionRequest):
    from engine.story_comments import report, moderate, CommentError
    uid = parse_user_id(req.user_id)
    try:
        if req.action == "report":
            return {"status": "ok", "reports": report(uid, comment_id, req.reason)}
        moderate(uid, comment_id, req.action)
        return {"status": "ok"}
    except CommentError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/leaderboard")
def serve_leaderboard():
    return FileResponse(os.path.join(os.path.dirname(__file__), "story.html"))


@app.get("/history")
def serve_history():
    return FileResponse(os.path.join(os.path.dirname(__file__), "story.html"))


@app.get("/api/stories/history/{user_id_str}")
def api_story_history(user_id_str: str):
    """Every story call one user made (from the trade log), with totals and founding seat."""
    from engine.story_stats import user_history
    uid = parse_user_id(user_id_str)
    session = SessionLocal()
    try:
        d = user_history(session, uid)
        d["name"] = get_display_name(uid)
        return d
    finally:
        session.close()


@app.get("/api/stories/leaderboard")
def api_story_leaderboard(period: str = "all", user_id: str = None):
    from engine.story_stats import leaderboard
    period = "week" if period == "week" else "all"
    session = SessionLocal()
    try:
        d = leaderboard(session, period, get_display_name, limit=1000)
        if user_id:
            me = next((r for r in d["rows"] if r["user_id"] == str(parse_user_id(user_id))), None)
            d["me"] = me
        d["rows"] = d["rows"][:100]
        return d
    finally:
        session.close()


@app.get("/api/narrative/history")
def api_narrative_history(hours: float = 168.0, limit: int = 500):
    """FSNI tick history for the chart. Default: 7 days, newest `limit` readings.
    Only readings made with the baseline-relative scoring (payload has "raw")
    are returned, so earlier test calibrations don't distort the chart."""
    import time as _time
    import sqlalchemy as _sa
    cutoff = _time.time() - hours * 3600
    limit = max(1, min(int(limit), 2000))
    session = SessionLocal()
    try:
        rows = session.execute(
            _sa.text(
                "SELECT value, intensity_score, diffusion_score, ts "
                "FROM index_ticks WHERE ts >= :cutoff AND headlines LIKE :pat "
                "ORDER BY ts DESC LIMIT :lim"
            ),
            {"cutoff": cutoff, "lim": limit, "pat": '%"raw":%'}
        ).fetchall()
        rows = list(reversed(rows))
        ticks = [
            {"value": round(float(r[0]), 2),
             "intensity_score": round(float(r[1] or 0), 2),
             "diffusion_score": round(float(r[2] or 0), 2),
             "ts": float(r[3])}
            for r in rows
        ]
        return {"ticks": ticks, "count": len(ticks)}
    finally:
        session.close()
