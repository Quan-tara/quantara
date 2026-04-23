from db.models import Trade, Position, Contract
from engine.wallet import transfer_cash, lock_collateral, unlock_collateral
from engine.constants import MM_USER_ID
import uuid


# =========================================================
# PRIMARY TRADE EXECUTION
# ─────────────────────────────────────────────────────────
# HOLDER (buyer):  pays premium, receives collateral on YES
# WRITER (seller): locks collateral, collects premium, pays on YES
#
# MM CAN BE EITHER SIDE:
#   MM_SELL: MM acts as WRITER → locks collateral, collects premium
#   MM_BUY:  MM acts as HOLDER → pays premium (rare, via !quote bid)
#
# The MM is exposed to settlement risk. This is intentional:
# the MM profits from the spread (ask > bid) which over many
# contracts compensates for expected losses. The MM manages
# risk by quoting fairly (see quoting calculator on dashboard).
# =========================================================
def execute_primary_trade(
    session,
    buyer_id: int,
    seller_id: int,
    contract_id: int,
    price: float,
    quantity: float,
    buy_order_id: int = None,
    sell_order_id: int = None
):
    contract = session.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise ValueError(f"Contract {contract_id} not found")

    total_premium    = price * quantity
    total_collateral = contract.collateral * quantity

    # trade type
    if buyer_id == MM_USER_ID:
        trade_type = "MM_BUY"
    elif seller_id == MM_USER_ID:
        trade_type = "MM_SELL"
    else:
        trade_type = "USER_TRADE"

    # record trade
    trade = Trade(
        contract_id   = contract_id,
        buyer_id      = buyer_id,
        seller_id     = seller_id,
        price         = price,
        quantity      = quantity,
        trade_type    = trade_type,
        buy_order_id  = buy_order_id,
        sell_order_id = sell_order_id
    )
    session.add(trade)
    session.flush()

    # cash: buyer pays premium to seller
    transfer_cash(session, from_id=buyer_id, to_id=seller_id, amount=total_premium)

    # HOLDER position for buyer (includes MM when MM buys)
    session.add(Position(
        id          = str(uuid.uuid4()),
        contract_id = contract_id,
        user_id     = buyer_id,
        role        = "HOLDER",
        quantity    = quantity,
        premium     = price,
        collateral  = contract.collateral,
        status      = "OPEN",
        trade_id    = trade.id
    ))
    print(f"📦 HOLDER created → user {buyer_id} | paid {price} | collateral {contract.collateral}")

    # WRITER position for seller + lock collateral (includes MM when MM sells)
    session.add(Position(
        id          = str(uuid.uuid4()),
        contract_id = contract_id,
        user_id     = seller_id,
        role        = "WRITER",
        quantity    = quantity,
        premium     = price,
        collateral  = contract.collateral,
        status      = "OPEN",
        trade_id    = trade.id
    ))
    lock_collateral(session, user_id=seller_id, amount=total_collateral)
    print(f"🔒 WRITER created + collateral locked → user {seller_id}: {total_collateral}")

    return trade


# =========================================================
# SECONDARY TRADE EXECUTION
# =========================================================
def execute_secondary_trade(
    session,
    buyer_id: int,
    seller_id: int,
    contract_id: int,
    price: float,
    quantity: float,
    seller_position_id: str,
    buy_order_id: int = None,
    sell_order_id: int = None
):
    from db.models import Order

    old_position = session.query(Position).filter(
        Position.id      == seller_position_id,
        Position.user_id == seller_id,
        Position.status  == "OPEN"
    ).first()

    if not old_position:
        raise ValueError(
            f"Position {seller_position_id} not found or not owned by seller {seller_id}."
        )

    total_price      = price * quantity
    total_collateral = old_position.collateral * quantity

    trade = Trade(
        contract_id   = contract_id,
        buyer_id      = buyer_id,
        seller_id     = seller_id,
        price         = price,
        quantity      = quantity,
        trade_type    = "SECONDARY",
        buy_order_id  = buy_order_id,
        sell_order_id = sell_order_id
    )
    session.add(trade)
    session.flush()

    transfer_cash(session, from_id=buyer_id, to_id=seller_id, amount=total_price)

    old_position.status = "TRANSFERRED"
    session.flush()
    print(f"🔒 Position {old_position.id[:8]} TRANSFERRED — seller {seller_id}")

    # cancel ghost secondary sell orders
    ghost_orders = session.query(Order).filter(
        Order.position_id == seller_position_id,
        Order.status      == "OPEN"
    ).all()
    for go in ghost_orders:
        go.status = "CANCELLED"
    session.flush()

    new_position = Position(
        id          = str(uuid.uuid4()),
        contract_id = contract_id,
        user_id     = buyer_id,
        role        = old_position.role,
        quantity    = quantity,
        premium     = price,           # what the buyer actually paid on secondary market
        collateral  = old_position.collateral,
        status      = "OPEN",
        trade_id    = trade.id
    )
    session.add(new_position)
    session.flush()
    print(f"📦 New {new_position.role} created → buyer {buyer_id} (id: {new_position.id[:8]})")

    if old_position.role == "WRITER":
        unlock_collateral(session, user_id=seller_id, amount=total_collateral)
        lock_collateral(session,   user_id=buyer_id,  amount=total_collateral)
        print(f"🔄 Collateral {total_collateral} transferred: {seller_id} → {buyer_id}")

    return trade


# =========================================================
# CANCEL ORDER
# =========================================================
def cancel_order(session, user_id: int, order_id: int):
    from db.models import Order

    order = session.query(Order).filter(Order.id == order_id).first()
    if not order:               return "ORDER_NOT_FOUND"
    if order.user_id != user_id: return "NOT_OWNER"
    if order.status != "OPEN":   return "CANNOT_CANCEL"

    order.status = "CANCELLED"
    session.commit()
    return "CANCELLED"


# =========================================================
# TRADE HISTORY
# =========================================================
def get_trades(contract_id: int = None, limit: int = 10):
    from db.database import SessionLocal

    session = SessionLocal()
    try:
        query = session.query(Trade).order_by(Trade.id.desc())
        if contract_id:
            query = query.filter(Trade.contract_id == contract_id)
        trades = query.limit(limit).all()
        return [
            {
                "id":          t.id,
                "contract_id": t.contract_id,
                "price":       t.price,
                "quantity":    t.quantity,
                "buyer_id":    t.buyer_id,
                "seller_id":   t.seller_id,
                "trade_type":  t.trade_type,
                "created_at":  str(t.created_at)
            }
            for t in trades
        ]
    finally:
        session.close()
