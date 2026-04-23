from db.database import SessionLocal
from db.models import Order, Contract
from engine.execution import execute_primary_trade, execute_secondary_trade
from engine.constants import MM_USER_ID
from datetime import datetime


# =========================================================
# CORE MATCHING ENGINE
# ─────────────────────────────────────────────────────────
# CRITICAL RULE — TWO SEPARATE LANES:
#
#   PRIMARY order   → only matches against PRIMARY orders
#   SECONDARY order → only matches against SECONDARY orders
#
# This prevents a secondary BUY from accidentally hitting
# the MM's primary ASK instead of a seller's listed position.
# =========================================================
def match_orders(order_id: int):
    session = SessionLocal()

    try:
        new_order = session.query(Order).filter(Order.id == order_id).first()

        if not new_order:
            print(f"❌ MATCH: order {order_id} not found")
            return "NO_ORDER"

        print(f"🔥 MATCH: id={order_id} side={new_order.side} price={new_order.price} "
              f"qty={new_order.quantity} type={new_order.order_type}")

        while True:
            remaining = new_order.quantity - new_order.filled
            if remaining <= 0:
                break

            # -----------------------------------------------
            # FIND COUNTERPARTY
            # Must be same contract, same order_type, opposite
            # side, price crosses, different user.
            # -----------------------------------------------
            if new_order.side == "BUY":
                counterparty = (
                    session.query(Order)
                    .filter(Order.contract_id == new_order.contract_id)
                    .filter(Order.order_type  == new_order.order_type)   # ← SAME LANE
                    .filter(Order.side        == "SELL")
                    .filter(Order.status      == "OPEN")
                    .filter(Order.price       <= new_order.price)
                    .filter(Order.user_id     != new_order.user_id)
                    .order_by(Order.price.asc(), Order.created_at.asc())
                    .first()
                )
            else:
                counterparty = (
                    session.query(Order)
                    .filter(Order.contract_id == new_order.contract_id)
                    .filter(Order.order_type  == new_order.order_type)   # ← SAME LANE
                    .filter(Order.side        == "BUY")
                    .filter(Order.status      == "OPEN")
                    .filter(Order.price       >= new_order.price)
                    .filter(Order.user_id     != new_order.user_id)
                    .order_by(Order.price.desc(), Order.created_at.asc())
                    .first()
                )

            if not counterparty:
                print(f"📭 No {new_order.order_type} counterparty — order resting")
                break

            traded_qty  = min(
                new_order.quantity    - new_order.filled,
                counterparty.quantity - counterparty.filled
            )
            trade_price = counterparty.price

            print(f"💥 MATCH ({new_order.order_type}): {traded_qty} @ {trade_price}")

            # assign buyer/seller
            if new_order.side == "BUY":
                buyer_order  = new_order
                seller_order = counterparty
            else:
                buyer_order  = counterparty
                seller_order = new_order

            # route to correct execution
            if new_order.order_type == "SECONDARY":
                if not seller_order.position_id:
                    print("❌ SECONDARY SELL has no position_id — cannot execute")
                    break

                print(f"🔄 SECONDARY: position {seller_order.position_id}")
                execute_secondary_trade(
                    session            = session,
                    buyer_id           = buyer_order.user_id,
                    seller_id          = seller_order.user_id,
                    contract_id        = new_order.contract_id,
                    price              = trade_price,
                    quantity           = traded_qty,
                    seller_position_id = seller_order.position_id,
                    buy_order_id       = buyer_order.id,
                    sell_order_id      = seller_order.id
                )
            else:
                print(f"🆕 PRIMARY")
                execute_primary_trade(
                    session       = session,
                    buyer_id      = buyer_order.user_id,
                    seller_id     = seller_order.user_id,
                    contract_id   = new_order.contract_id,
                    price         = trade_price,
                    quantity      = traded_qty,
                    buy_order_id  = buyer_order.id,
                    sell_order_id = seller_order.id
                )

            # update fill
            new_order.filled    += traded_qty
            counterparty.filled += traded_qty

            if counterparty.filled >= counterparty.quantity:
                counterparty.status = "FILLED"

            if new_order.filled >= new_order.quantity:
                new_order.status = "FILLED"
                break

        session.commit()
        print("✅ MATCH COMPLETE")
        return "MATCHED"

    except Exception as e:
        session.rollback()
        print(f"❌ MATCH ERROR: {e}")
        return f"ERROR: {e}"

    finally:
        session.close()
