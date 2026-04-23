from db.database import SessionLocal
from db.models import Contract, User
from engine.users import get_or_create_user
from datetime import datetime, timedelta


# =========================================================
# CREATE CONTRACT (internal system object)
# =========================================================
def create_contract(event_name: str, payout: float, premium: float):
    """
    NOTE:
    In v2 this is NOT used by traders directly.
    Contracts are created via:
    - MM quotes OR
    - orderbook matching engine
    """

    session = SessionLocal()

    try:
        contract = Contract(
            event_name=event_name,
            payout=payout,          # collateral size reference
            premium=premium,
            status="OPEN"
        )

        contract.expires_at = datetime.utcnow() + timedelta(minutes=10)

        session.add(contract)
        session.commit()
        session.refresh(contract)

        return contract

    except Exception as e:
        session.rollback()
        return f"ERROR: {e}"

    finally:
        session.close()


# =========================================================
# SELL SIDE (LIQUIDITY PROVIDER)
# =========================================================
def sell_contract(user_id: int, contract_id: int):
    """
    Seller provides collateral and agrees to risk exposure
    """

    session = SessionLocal()

    try:
        user = get_or_create_user(user_id)
        contract = session.query(Contract).filter(Contract.id == contract_id).first()

        if not contract:
            return "❌ Contract not found"

        if contract.status != "OPEN":
            return "❌ Contract not open"

        # collateral check
        if user.balance < contract.payout:
            return "❌ Insufficient balance for collateral"

        # lock collateral
        user.balance -= contract.payout
        user.locked_collateral += contract.payout

        contract.seller_id = user_id
        contract.status = "FUNDED"

        session.commit()

        return f"🟥 Seller locked collateral for contract {contract_id}"

    except Exception as e:
        session.rollback()
        return f"❌ SELL ERROR: {e}"

    finally:
        session.close()


# =========================================================
# BUY SIDE (TAKER OF PROTECTION)
# =========================================================
def buy_contract(user_id: int, contract_id: int):
    """
    Buyer pays premium to enter contract
    """

    session = SessionLocal()

    try:
        user = get_or_create_user(user_id)
        contract = session.query(Contract).filter(Contract.id == contract_id).first()

        if not contract:
            return "❌ Contract not found"

        if contract.status != "FUNDED":
            return "❌ Contract not ready (no seller yet)"

        if contract.buyer_id is not None:
            return "❌ Already filled"

        if user.balance < contract.premium:
            return "❌ Not enough balance"

        seller = session.query(User).filter(User.id == contract.seller_id).first()

        if not seller:
            return "❌ Seller not found"

        # transfer premium to seller
        user.balance -= contract.premium
        seller.balance += contract.premium

        contract.buyer_id = user_id
        contract.status = "ACTIVE"

        session.commit()

        return f"🟦 Buyer entered contract {contract_id}"

    except Exception as e:
        session.rollback()
        return f"❌ BUY ERROR: {e}"

    finally:
        session.close()


# =========================================================
# SETTLEMENT ENGINE (AUTOMATIC TRIGGER)
# =========================================================
def settle_contract(contract_id: int, result: str):
    """
    result = "YES" or "NO"
    """

    session = SessionLocal()

    try:
        contract = session.query(Contract).filter(Contract.id == contract_id).first()

        if not contract:
            return "❌ Contract not found"

        if contract.status != "ACTIVE":
            return f"❌ Cannot settle (status={contract.status})"

        buyer = session.query(User).filter(User.id == contract.buyer_id).first()
        seller = session.query(User).filter(User.id == contract.seller_id).first()

        if not buyer or not seller:
            return "❌ Missing buyer or seller"

        result = result.upper()

        if result not in ["YES", "NO"]:
            return "❌ Invalid result"

        contract.result = result

        # =====================================================
        # SETTLEMENT LOGIC
        # =====================================================

        if result == "YES":
            # Buyer wins collateral
            seller.locked_collateral -= contract.payout
            buyer.balance += contract.payout

        elif result == "NO":
            # Seller keeps collateral
            seller.locked_collateral -= contract.payout
            seller.balance += contract.payout

        contract.status = "SETTLED"
        contract.settled_at = datetime.utcnow()

        session.commit()

        return f"✅ Contract {contract_id} settled as {result}"

    except Exception as e:
        session.rollback()
        return f"❌ SETTLE ERROR: {e}"

    finally:
        session.close()


# =========================================================
# LIST CONTRACTS (PUBLIC VIEW)
# =========================================================
def list_contracts():
    session = SessionLocal()

    try:
        contracts = session.query(Contract).all()

        if not contracts:
            return "📭 No contracts"

        output = []

        for c in contracts:
            output.append(
                f"ID {c.id} | {c.event_name} | "
                f"Premium {c.premium} | Collateral {c.payout} | "
                f"Status {c.status}"
            )

        return "\n".join(output)

    finally:
        session.close()