from db.database import SessionLocal
from db.models import Position
from engine.wallet import transfer_cash


# =========================================================
# TRANSFER POSITION (CLEAN OWNERSHIP MODEL)
# =========================================================
def transfer_position(position_id: str, from_user: int, to_user: int, price: float):
    session = SessionLocal()

    try:
        position = session.query(Position)\
            .filter(Position.id == position_id)\
            .first()

        if not position:
            return "POSITION_NOT_FOUND"

        if position.user_id != from_user:
            return "NOT_OWNER"

        if position.status != "OPEN":
            return f"INVALID_STATUS_{position.status}"

        # ----------------------------------------
        # CASH SETTLEMENT BETWEEN USERS
        # ----------------------------------------
        transfer_cash(
            session,
            from_id=to_user,
            to_id=from_user,
            amount=price
        )

        # ----------------------------------------
        # OWNERSHIP TRANSFER
        # ----------------------------------------
        position.user_id = to_user

        # ----------------------------------------
        # COLLATERAL FOLLOWS POSITION (IMPORTANT RULE)
        # ----------------------------------------
        # NO CHANGE — but we explicitly enforce consistency
        position.locked_collateral = position.locked_collateral

        session.commit()

        return {
            "status": "TRANSFER_OK",
            "position_id": position_id,
            "from": from_user,
            "to": to_user,
            "price": price,
            "collateral": position.locked_collateral
        }

    except Exception as e:
        session.rollback()
        return f"ERROR: {e}"

    finally:
        session.close()


# =========================================================
# POSITION VIEW (USER PORTFOLIO)
# =========================================================
def get_positions(user_id: int):
    session = SessionLocal()

    try:
        positions = session.query(Position)\
            .filter(Position.user_id == user_id)\
            .filter(Position.status == "OPEN")\
            .all()

        if not positions:
            return "📭 No positions"

        msg = ""

        for p in positions:
            msg += (
                f"ID: {p.id} | "
                f"Side: {p.side} | "
                f"Qty: {p.quantity} | "
                f"Entry: {p.entry_price} | "
                f"Collateral: {p.locked_collateral}\n"
            )

        return msg

    finally:
        session.close()


# =========================================================
# POSITION VALUE (OPTIONAL DEBUG TOOL)
# =========================================================
def calculate_position_value(position, mark_price: float):
    """
    Not persisted — purely derived value
    """

    if position.side == "LONG":
        return (mark_price - position.entry_price) * position.quantity

    elif position.side == "SHORT":
        return (position.entry_price - mark_price) * position.quantity

    return 0.0

def lock_collateral(position, amount: float):
    """
    Assign locked collateral to a position
    """
    position.locked_collateral = amount