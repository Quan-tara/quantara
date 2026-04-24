from db.database import SessionLocal
from db.models import User, Position, Order
from engine.wallet import unlock_collateral


# =========================================================
# GET OR CREATE USER
# =========================================================
def get_or_create_user(user_id: int) -> User:
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if user:
            session.expunge(user)
            return user

        user = User(id=user_id, balance=100000.0, locked_collateral=0.0)
        session.add(user)
        session.commit()
        session.refresh(user)
        session.expunge(user)
        return user

    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


# =========================================================
# CANCEL POSITION
# Closes a position early — before settlement.
# HOLDER: just closes, loses remaining premium value (no refund)
# WRITER: closes AND unlocks collateral back to wallet
# Also cancels any open secondary sell orders on this position.
# =========================================================
def cancel_position(user_id: int, short_id: str) -> str:
    session = SessionLocal()
    try:
        # resolve short ID to full position
        positions = session.query(Position).filter(
            Position.user_id == user_id,
            Position.status  == "OPEN"
        ).all()

        position = next((p for p in positions if p.id.startswith(short_id)), None)

        if not position:
            return f"❌ Position `{short_id}` not found or not owned by you"

        # cancel any open secondary sell orders on this position
        open_orders = session.query(Order).filter(
            Order.position_id == position.id,
            Order.status      == "OPEN"
        ).all()

        for o in open_orders:
            o.status = "CANCELLED"
            print(f"🗑️ Cancelled secondary order {o.id} for position {position.id}")

        # unlock collateral if WRITER
        if position.role == "WRITER":
            total_collateral = position.collateral * position.quantity
            unlock_collateral(session, user_id=user_id, amount=total_collateral)
            print(f"🔓 Collateral unlocked for WRITER {user_id}: {total_collateral}")

        position.status = "CANCELLED"

        # If HOLDER cancels, find the matching WRITER position on the same contract
        # and release their collateral immediately — no point keeping it locked.
        if position.role == "HOLDER":
            matching_writers = session.query(Position).filter(
                Position.contract_id == position.contract_id,
                Position.user_id     == 0,  # MM_USER_ID
                Position.status      == "OPEN",
                Position.role        == "WRITER",
                Position.quantity    == position.quantity
            ).first()
            if matching_writers:
                total_collateral = matching_writers.collateral * matching_writers.quantity
                from engine.wallet import unlock_collateral
                unlock_collateral(session, user_id=0, amount=total_collateral)
                matching_writers.status = "CANCELLED"
                print(f"🔓 MM collateral {total_collateral:.2f} released — HOLDER cancelled early")

        session.commit()

        role_msg = (
            f"🔓 Collateral of {position.collateral * position.quantity:.2f} unlocked."
            if position.role == "WRITER"
            else "💸 Premium forfeited. MM collateral released immediately."
        )

        return (
            f"✅ Position `{short_id}` cancelled.\n"
            f"Role: {position.role} | Contract: {position.contract_id}\n"
            f"{role_msg}"
        )

    except Exception as e:
        session.rollback()
        return f"❌ ERROR: {e}"
    finally:
        session.close()


# =========================================================
# POSITIONS WITH PNL — delegates to pnl.py
# =========================================================
def get_positions_with_pnl(user_id: int):
    from engine.pnl import get_positions_with_pnl as _get
    return _get(user_id)
