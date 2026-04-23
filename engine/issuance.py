from dataclasses import dataclass
from engine.orderbook import place_order


# =========================================================
# RISK INSTRUMENT DEFINITION
# =========================================================
@dataclass
class RiskInstrument:
    name: str
    collateral: float
    premium: float


# =========================================================
# ISSUER (YOU CONTROL MARKET PRODUCTS)
# =========================================================
class Issuer:

    def __init__(self, mm_user_id: int):
        self.mm_user_id = mm_user_id

    # -----------------------------------------------------
    # CREATE MARKET LIQUIDITY (PRIMARY MARKET SEEDING)
    # -----------------------------------------------------
    def launch_instrument(self, instrument: RiskInstrument, liquidity: float = 1.0):
        """
        Seeds market with initial bid/ask liquidity
        """

        mid = instrument.premium

        bid = mid * 0.95
        ask = mid * 1.05

        # Provide initial market structure via MM account
        place_order(
            user_id=self.mm_user_id,
            side="BUY",
            price=bid,
            quantity=liquidity
        )

        place_order(
            user_id=self.mm_user_id,
            side="SELL",
            price=ask,
            quantity=liquidity
        )

        return {
            "instrument": instrument.name,
            "bid": bid,
            "ask": ask
        }