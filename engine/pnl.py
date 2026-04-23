from db.database import SessionLocal
from db.models import Position, Trade, Wallet
from engine.index_provider import get_risk_index
from engine.constants import MM_USER_ID


# =========================================================
# PRICING FORMULA
# ─────────────────────────────────────────────────────────
# The entry_price stored on a position is the market-implied
# expected value at the time of the trade — i.e. the price
# the market said the position was worth when you bought it.
#
# mark_price  = collateral × (index / 100)
#             = the current market-implied expected payout
#
# At index=10:  mark = collateral × 0.10  (10% chance of payout)
# At index=50:  mark = collateral × 0.50  (50% chance of payout)
# At index=80:  mark = collateral × 0.80  (80% chance of payout)
#
# HOLDER PnL = (mark_price - entry_price) × qty
#   Positive when risk has risen since entry (market moved in your favour)
#   Negative when risk has fallen since entry
#
# WRITER PnL = (entry_price - mark_price) × qty
#   Positive when risk has fallen (your obligation is worth less)
#   Negative when risk has risen (your obligation is worth more)
# =========================================================

def calc_mark_price(collateral: float, index: float) -> float:
    """Current fair value of a position: collateral × (index/100)."""
    return round(collateral * (index / 100), 2)


def calc_pnl(role: str, entry_price: float, collateral: float,
             index: float, quantity: float) -> float:
    mark = calc_mark_price(collateral, index)
    if role == "HOLDER":
        return round((mark - entry_price) * quantity, 2)
    else:  # WRITER
        return round((entry_price - mark) * quantity, 2)


# kept for backward-compat with web/app.py imports
def calc_market_price(premium: float, index: float) -> float:
    """
    Legacy alias — now unused for PnL but kept so imports don't break.
    Callers should use calc_mark_price(collateral, index) instead.
    """
    return round(premium * (index / 100), 2)


# =========================================================
# USER PNL (for !pnl command)
# =========================================================
def get_user_pnl(user_id: int):
    session = SessionLocal()
    try:
        positions = session.query(Position).filter(
            Position.user_id == user_id,
            Position.status  == "OPEN"
        ).all()

        if not positions:
            return "📭 No open positions"

        index     = get_risk_index()
        total_pnl = 0.0
        lines     = []

        for p in positions:
            mark = calc_mark_price(p.collateral, index)
            pnl  = calc_pnl(p.role, p.premium, p.collateral, index, p.quantity)
            total_pnl += pnl
            lines.append(
                f"  {p.role} | Contract {p.contract_id} | "
                f"Paid {p.premium:.2f} | Mark {mark:.2f} | PnL {pnl:+.2f}"
            )

        return (f"📊 **PnL (Risk Index: {index:.1f})**\n"
                + "\n".join(lines)
                + f"\n\n**Total: {total_pnl:+.2f}**")

    finally:
        session.close()


# =========================================================
# POSITIONS WITH PNL (for !positions command)
# =========================================================
def get_positions_with_pnl(user_id: int):
    session = SessionLocal()
    try:
        positions = session.query(Position).filter(
            Position.user_id == user_id,
            Position.status  == "OPEN"
        ).all()

        if not positions:
            return "📭 No open positions"

        index = get_risk_index()
        msg   = f"📋 **YOUR POSITIONS (Risk Index: {index:.1f})**\n\n"

        for p in positions:
            mark = calc_mark_price(p.collateral, index)
            pnl  = calc_pnl(p.role, p.premium, p.collateral, index, p.quantity)
            msg += (
                f"**{p.role}** | Contract {p.contract_id}\n"
                f"  Short ID: `{p.id[:8]}` (use for !offer)\n"
                f"  Qty: {p.quantity} | Paid: {p.premium:.2f} | "
                f"Collateral: {p.collateral:.2f}\n"
                f"  Mark: {mark:.2f} | PnL: {pnl:+.2f}\n\n"
            )

        return msg

    finally:
        session.close()


# =========================================================
# MM SPREAD PNL
# =========================================================
def get_mm_pnl():
    session = SessionLocal()
    try:
        mm_sells = session.query(Trade).filter(
            Trade.seller_id  == MM_USER_ID,
            Trade.trade_type == "MM_SELL"
        ).all()
        mm_buys = session.query(Trade).filter(
            Trade.buyer_id   == MM_USER_ID,
            Trade.trade_type == "MM_BUY"
        ).all()

        total_received = sum(t.price * t.quantity for t in mm_sells)
        total_paid     = sum(t.price * t.quantity for t in mm_buys)
        spread_pnl     = total_received - total_paid

        wallet = session.query(Wallet).filter(Wallet.user_id == MM_USER_ID).first()
        cash   = wallet.cash_balance if wallet else 10000.0

        return {
            "spread_pnl":     round(spread_pnl, 2),
            "total_received": round(total_received, 2),
            "total_paid":     round(total_paid, 2),
            "sell_trades":    len(mm_sells),
            "buy_trades":     len(mm_buys),
            "wallet_balance": round(cash, 2)
        }
    finally:
        session.close()
