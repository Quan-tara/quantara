from db.database import SessionLocal
from db.models import Position, Contract, PublishedRate
from engine.wallet import transfer_cash, unlock_collateral, get_wallet
from datetime import datetime
import threading
import time


# =========================================================
# CORE SETTLEMENT — double-entry, cash-conserving
# =========================================================
def settle_contract(contract_id: int, result: str):
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

        contract.status     = "SETTLED"
        contract.result     = result
        contract.settled_at = datetime.utcnow()
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
# Runs every 60s. Settles all expired auto_settle contracts
# using the current published daily rate.
# =========================================================
def auto_settle_expired():
    """Check for expired auto_settle contracts and settle them."""
    session = SessionLocal()
    try:
        from engine.index_provider import get_current_published_rate
        now = datetime.utcnow()

        expired = session.query(Contract).filter(
            Contract.status      == "OPEN",
            Contract.auto_settle == True,
            Contract.expires_at  <= now
        ).all()

        if not expired:
            return

        pub = get_current_published_rate()
        rate      = pub["rate"]
        threshold = pub["threshold"]
        result    = "YES" if rate > threshold else "NO"

        for c in expired:
            cid = c.id
            session.close()
            msg = settle_contract(cid, result)
            print(f"⏰ Auto-settled Contract #{cid} as {result} "
                  f"(rate {rate:.1f}% vs threshold {threshold:.1f}%): {msg[:60]}")
            session = SessionLocal()

            # Auto-launch next instance of this series
            if c.series_id:
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
