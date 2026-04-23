from engine.pricing import get_mark_price
from engine.constants import MM_USER_ID
from engine.orderbook import place_order

# =========================================================
# MARKET MAKER ENGINE (LIQUIDITY PROVIDER)
# =========================================================

MM_USER_ID = 0


class MarketMaker:

    def __init__(self, user_id: int):
        self.user_id = user_id

    # -----------------------------------------------------
    # IDENTITY CHECK
    # -----------------------------------------------------
    def is_mm(self) -> bool:
        return self.user_id == MM_USER_ID

    # -----------------------------------------------------
    # BASIC QUOTE GENERATION
    # -----------------------------------------------------
    def generate_quotes(self, spread: float = 0.2):
        """
        Creates bid/ask around mark price
        """

        mid = get_mark_price()

        bid = mid - spread
        ask = mid + spread

        return bid, ask

    # -----------------------------------------------------
    # POST LIQUIDITY INTO ORDERBOOK
    # -----------------------------------------------------
    def quote(self, quantity: float = 1.0, spread: float = 0.2):
        """
        Places MM orders into the system
        """

        bid, ask = self.generate_quotes(spread)

        # BUY SIDE (MM bid)
        place_order(
            user_id=self.user_id,
            side="BUY",
            price=bid,
            quantity=quantity,
            trade_meta={"source": "MM"}
        )

        # SELL SIDE (MM ask)
        place_order(
            user_id=self.user_id,
            side="SELL",
            price=ask,
            quantity=quantity,
            trade_meta={"source": "MM"}
        )

        return {
            "bid": bid,
            "ask": ask,
            "spread": ask - bid
        }