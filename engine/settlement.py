from db.database import SessionLocal
from db.models import Position, Contract, PublishedRate, ContractSeries
from engine.wallet import transfer_cash, unlock_collateral, get_wallet
from datetime import datetime
import threading
import time


# =========================================================
# CANCEL ALL OPEN ORDERS ON A CONTRACT
# Called before settlement and before launching a new contract.
# Ensures no stale orders persist across contract instances.
# =========================================================
def _cancel_contract_orders(session, contract_id: int):
    """Cancel all open orders for a contract. Returns count cancelled."""
    from db.models import Order
    open_orders = session.query(Order).filter(
        Order.contract_id == contract_id,
        Order.status      == "OPEN"
    ).all()
    for o in open_orders:
        o.status = "CANCELLED"
    session.commit()
    count = len(open_orders)
    if count:
        print(f"🗑️ Cancelled {count} open orders for contract #{contract_id}")
    return count


# =========================================================
# CORE SETTLEMENT — double-entry, cash-conserving
# =========================================================
def settle_contract(contract_id: int, result: str, settlement_rate: float = None):
    session = SessionLocal()
    try:
        result = result.upper()
        if result not in ["YES", "NO"]:
            return "❌ Result must be YES or NO"

        contract = session.query(Contract).filter(Contract.id == contract_id).first()
        if not contract:
            return f"❌ Contract {contract_id} not found"
        if contract.status != "OPEN":
            return f"❌ Contract already {contract.status}"

        positions = session.query(Position).filter(
            Position.contract_id == contract_id,
            Position.status      == "OPEN"
        ).all()

        summary = []

        if result == "YES":
            holders = {p.trade_id: p for p in positions if p.role == "HOLDER"}
            writers = {p.trade_id: p for p in positions if p.role == "WRITER"}

            for trade_id, holder in holders.items():
                writer = writers.get(trade_id)
                total_col = holder.collateral * holder.quantity
                if writer:
                    unlock_collateral(session, user_id=writer.user_id, amount=total_col)
                    w_wallet = get_wallet(session, writer.user_id)
                    w_wallet.cash_balance -= total_col
                    h_wallet = get_wallet(session, holder.user_id)
                    h_wallet.cash_balance += total_col
                    summary.append(f"✅ HOLDER {holder.user_id} +{total_col:.2f}")
                    summary.append(f"💸 WRITER {writer.user_id} -{total_col:.2f}")
                    writer.status = "SETTLED"
                else:
                    summary.append(f"⚠️ HOLDER {holder.user_id} — no matching WRITER")
                holder.status = "SETTLED"

            for trade_id, writer in writers.items():
                if writer.status != "SETTLED":
                    total_col = writer.collateral * writer.quantity
                    unlock_collateral(session, user_id=writer.user_id, amount=total_col)
                    writer.status = "SETTLED"

        elif result == "NO":
            for p in positions:
                if p.role == "WRITER":
                    unlock_collateral(session, user_id=p.user_id,
                                      amount=p.collateral * p.quantity)
                    summary.append(f"✅ WRITER {p.user_id} collateral returned")
                elif p.role == "HOLDER":
                    summary.append(f"💸 HOLDER {p.user_id} loses premium")
                p.status = "SETTLED"

        # Cancel all remaining open orders before closing the contract
        _cancel_contract_orders(session, contract_id)

        contract.status     = "SETTLED"
        contract.result     = result
        contract.settled_at = datetime.utcnow()
        if settlement_rate is not None:
            contract.settlement_rate = settlement_rate
        session.commit()

        lines = "\n".join(summary) if summary else "(no open positions)"
        return f"✅ Contract {contract_id} settled as **{result}**\n{lines}"

    except Exception as e:
        session.rollback()
        return f"❌ SETTLEMENT ERROR: {e}"
    finally:
        session.close()


# =========================================================
# AUTO-SETTLEMENT AGAINST PUBLISHED RATE
# =========================================================
# Per-duration settlement rate calculator
# =========================================================
def _get_window_rate(session, contract):
    """Calculate settlement rate for a contract based on its own duration window."""
    import sqlalchemy as _sa
    import time as _time
    from datetime import timedelta
    from engine.index_provider import get_current_published_rate

    expires_at = contract.expires_at
    created_at = contract.created_at
    threshold  = contract.settlement_threshold or 20.0

    duration_h = (expires_at - created_at).total_seconds() / 3600 if (created_at and expires_at) else 24

    # 24h contracts use the official published daily rate (includes noise term for realism)
    if 20 <= duration_h <= 28:
        pub = get_current_published_rate()
        return pub["rate"], threshold, "published daily rate"

    # All other durations: mean of index_ticks over the contract's own window
    window_start    = expires_at - timedelta(hours=duration_h)
    window_start_ts = _time.mktime(window_start.timetuple())
    window_end_ts   = _time.mktime(expires_at.timetuple())

    try:
        row = session.execute(
            _sa.text(
                "SELECT AVG(value), COUNT(*) FROM index_ticks "
                "WHERE ts >= :start AND ts <= :end"
            ),
            {"start": window_start_ts, "end": window_end_ts}
        ).fetchone()

        if row and row[1] and row[1] >= 6:
            return round(float(row[0]), 2), threshold, f"mean of {row[1]} ticks over {duration_h:.0f}h window"
        else:
            pub = get_current_published_rate()
            return pub["rate"], threshold, "published rate (fallback — insufficient ticks)"
    except Exception as e:
        print(f"⚠️ Window rate error: {e}")
        pub = get_current_published_rate()
        return pub["rate"], threshold, "published rate (fallback — error)"


# Runs every 60s. Settles all expired auto_settle contracts.
# Each contract settles against the mean index over its own duration window.
# 24h contracts use the official published daily rate (with noise).
# =========================================================
def auto_settle_expired():
    """Check for expired auto_settle contracts and settle them."""
    session = SessionLocal()
    try:
        now = datetime.utcnow()

        expired = session.query(Contract).filter(
            Contract.status      == "OPEN",
            Contract.auto_settle == True,
            Contract.expires_at  <= now
        ).all()

        if not expired:
            return

        for c in expired:
            cid = c.id
            # Per-contract settlement rate based on its own duration window
            rate, threshold, rate_desc = _get_window_rate(session, c)
            result = "YES" if rate > threshold else "NO"

            session.close()
            msg = settle_contract(cid, result, settlement_rate=rate)
            print(f"⏰ Auto-settled Contract #{cid} as {result} "
                  f"(rate {rate:.2f}% vs threshold {threshold:.1f}% — {rate_desc}): {msg[:60]}")
            session = SessionLocal()

            # Auto-launch next instance only if series is not paused
            if c.series_id:
                try:
                    # Use a brand-new session to avoid stale cache
                    from db.database import SessionLocal as _SL2
                    import sqlalchemy as _sa
                    _s2 = _SL2()
                    try:
                        row = _s2.execute(
                            _sa.text("SELECT paused FROM contract_series WHERE id = :sid"),
                            {"sid": c.series_id}
                        ).fetchone()
                        is_paused = bool(row[0]) if row and row[0] is not None else False
                    finally:
                        _s2.close()
                    if is_paused:
                        print(f"⏸ Series {c.series_id} is paused — skipping auto-launch")
                    else:
                        _auto_launch_series(c.series_id)
                except Exception as pe:
                    print(f"⚠️ Paused check error: {pe} — launching anyway")
                    _auto_launch_series(c.series_id)

    except Exception as e:
        print(f"❌ Auto-settle error: {e}")
    finally:
        try:
            session.close()
        except Exception:
            pass


def _auto_launch_series(series_id: int):
    """Launch a new contract instance for a series after it settles."""
    from db.models import ContractSeries
    from engine.index_provider import get_risk_index, get_current_published_rate
    from engine.constants import MM_USER_ID
    from engine.pnl import calc_mark_price

    session = SessionLocal()
    try:
        # Check no open contract already exists for this series
        existing = session.query(Contract).filter(
            Contract.series_id == series_id,
            Contract.status    == "OPEN"
        ).first()
        if existing:
            return

        series = session.query(ContractSeries).filter(
            ContractSeries.id == series_id
        ).first()
        if not series:
            return

        from datetime import timedelta
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
        print(f"🔄 Auto-launched Contract #{contract.id} for series {series_id} ({series.label})")
    except Exception as e:
        print(f"❌ Auto-launch error for series {series_id}: {e}")
        session.rollback()
    finally:
        session.close()


# =========================================================
# EXPIRY CHECKER LOOP
# =========================================================
def _expiry_checker():
    while True:
        time.sleep(60)
        try:
            auto_settle_expired()
        except Exception as e:
            print(f"❌ Expiry checker error: {e}")


_expiry_thread = threading.Thread(target=_expiry_checker, daemon=True)
_expiry_thread.start()
print("⏰ Auto-settlement checker started")
