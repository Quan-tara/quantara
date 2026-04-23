from db.database import SessionLocal
from db.models import Order, Contract, Position
from engine.matching_engine import match_orders
from engine.wallet import get_wallet
from engine.constants import MM_USER_ID
from datetime import datetime


# =========================================================
# RESOLVE SHORT POSITION ID → FULL UUID
# =========================================================
def resolve_position_id(session, user_id: int, short_or_full_id: str) -> str | None:
    """
    Accepts either a full UUID or an 8-char short ID.
    Returns the full UUID if found and owned by user, else None.
    """
    if not short_or_full_id:
        return None

    # Try exact match first (full UUID)
    pos = session.query(Position).filter(
        Position.id      == short_or_full_id,
        Position.user_id == user_id,
        Position.status  == "OPEN"
    ).first()
    if pos:
        return pos.id

    # Try prefix match (short ID)
    positions = session.query(Position).filter(
        Position.user_id == user_id,
        Position.status  == "OPEN"
    ).all()
    for p in positions:
        if p.id.startswith(short_or_full_id):
            return p.id

    return None


# =========================================================
# PLACE PRIMARY ORDER
# =========================================================
def place_order(user_id: int, contract_id: int, side: str, price: float, quantity: float):
    session = SessionLocal()
    try:
        contract = session.query(Contract).filter(Contract.id == contract_id).first()
        if not contract:
            return f"❌ Contract {contract_id} not found"
        if contract.status != "OPEN":
            return f"❌ Contract {contract_id} is {contract.status}"
        if contract.expires_at and contract.expires_at < datetime.utcnow():
            contract.status = "EXPIRED"
            session.commit()
            return "❌ Contract has expired"

        if side.upper() == "BUY" and user_id != MM_USER_ID:
            wallet = get_wallet(session, user_id)
            avail  = wallet.cash_balance - wallet.locked_balance
            if avail < price * quantity:
                return f"❌ Insufficient funds (need {price*quantity:.2f}, available {avail:.2f})"

        if side.upper() == "SELL" and user_id != MM_USER_ID:
            wallet    = get_wallet(session, user_id)
            required  = contract.collateral * quantity
            available = wallet.cash_balance - wallet.locked_balance
            if available < required:
                return f"❌ Insufficient collateral (need {required:.2f}, available {available:.2f})"

        order = Order(
            user_id     = user_id,
            contract_id = contract_id,
            side        = side.upper(),
            price       = price,
            quantity    = quantity,
            filled      = 0.0,
            order_type  = "PRIMARY",
            status      = "OPEN",
            created_at  = datetime.utcnow()
        )
        session.add(order)
        session.commit()
        session.refresh(order)

        print(f"📥 PRIMARY: id={order.id} user={user_id} side={side} price={price} qty={quantity}")
        match_orders(order.id)
        return {"id": order.id, "status": "PLACED"}

    except Exception as e:
        session.rollback()
        print(f"❌ PLACE ORDER ERROR: {e}")
        return f"❌ ERROR: {e}"
    finally:
        session.close()


# =========================================================
# PLACE SECONDARY ORDER
# Accepts short OR full position ID — resolves server-side.
# =========================================================
def place_secondary_order(user_id: int, contract_id: int, side: str,
                          price: float, quantity: float, position_id: str = None):
    session = SessionLocal()
    try:
        contract = session.query(Contract).filter(Contract.id == contract_id).first()
        if not contract:
            return f"❌ Contract {contract_id} not found"
        if contract.status != "OPEN":
            return f"❌ Contract {contract_id} is {contract.status}"

        full_position_id = None

        if side.upper() == "SELL":
            if not position_id:
                return "❌ Position ID required for secondary SELL"

            # Resolve short → full ID
            full_position_id = resolve_position_id(session, user_id, position_id)
            if not full_position_id:
                return (f"❌ Position `{position_id}` not found or not owned by you. "
                        f"Check !positions or your dashboard for valid Short IDs.")

        order = Order(
            user_id     = user_id,
            contract_id = contract_id,
            side        = side.upper(),
            price       = price,
            quantity    = quantity,
            filled      = 0.0,
            order_type  = "SECONDARY",
            position_id = full_position_id,
            status      = "OPEN",
            created_at  = datetime.utcnow()
        )
        session.add(order)
        session.commit()
        session.refresh(order)

        print(f"📥 SECONDARY: id={order.id} user={user_id} side={side} pos={full_position_id}")
        match_orders(order.id)
        return {"id": order.id, "status": "PLACED"}

    except Exception as e:
        session.rollback()
        print(f"❌ SECONDARY ORDER ERROR: {e}")
        return f"❌ ERROR: {e}"
    finally:
        session.close()


# =========================================================
# ORDER BOOK — per contract
# =========================================================
def get_order_book(contract_id: int):
    session = SessionLocal()
    try:
        bids = session.query(Order).filter(
            Order.contract_id == contract_id,
            Order.side   == "BUY",
            Order.status == "OPEN"
        ).order_by(Order.price.desc()).all()

        asks = session.query(Order).filter(
            Order.contract_id == contract_id,
            Order.side   == "SELL",
            Order.status == "OPEN"
        ).order_by(Order.price.asc()).all()

        return {
            "bids": [{"price": o.price, "qty": o.quantity - o.filled,
                      "type": o.order_type, "is_mm": o.user_id == MM_USER_ID} for o in bids],
            "asks": [{"price": o.price, "qty": o.quantity - o.filled,
                      "type": o.order_type, "is_mm": o.user_id == MM_USER_ID} for o in asks]
        }
    finally:
        session.close()


# =========================================================
# MARKET SNAPSHOT — per contract
# =========================================================
def get_market_snapshot(contract_id: int):
    session = SessionLocal()
    try:
        best_bid = session.query(Order).filter(
            Order.contract_id == contract_id,
            Order.side == "BUY", Order.status == "OPEN"
        ).order_by(Order.price.desc()).first()

        best_ask = session.query(Order).filter(
            Order.contract_id == contract_id,
            Order.side == "SELL", Order.status == "OPEN"
        ).order_by(Order.price.asc()).first()

        mid = round((best_bid.price + best_ask.price) / 2, 4) if best_bid and best_ask else None

        return {
            "contract_id": contract_id,
            "best_bid":    best_bid.price if best_bid else None,
            "best_ask":    best_ask.price if best_ask else None,
            "mid_price":   mid
        }
    finally:
        session.close()


# =========================================================
# UNIFIED ORDER BOOK — all contracts
# =========================================================
def get_all_orders():
    session = SessionLocal()
    try:
        orders = session.query(Order).filter(Order.status == "OPEN")\
            .order_by(Order.contract_id, Order.side, Order.price.desc()).all()
        return [
            {
                "id":          o.id,
                "contract_id": o.contract_id,
                "side":        o.side,
                "price":       o.price,
                "qty":         o.quantity - o.filled,
                "order_type":  o.order_type,
                "is_mm":       o.user_id == MM_USER_ID
            }
            for o in orders
        ]
    finally:
        session.close()
