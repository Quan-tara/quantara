from db.models import Wallet


# =========================================================
# GET OR CREATE WALLET
# =========================================================
def get_wallet(session, user_id: int) -> Wallet:
    wallet = session.query(Wallet).filter(Wallet.user_id == user_id).first()

    if not wallet:
        wallet = Wallet(user_id=user_id, cash_balance=100000.0, locked_balance=0.0)
        session.add(wallet)
        session.flush()

    return wallet


# =========================================================
# CASH TRANSFER
# =========================================================
def transfer_cash(session, from_id: int, to_id: int, amount: float):
    from_wallet = get_wallet(session, from_id)
    to_wallet   = get_wallet(session, to_id)

    if from_wallet.cash_balance < amount:
        raise ValueError(f"Insufficient cash: user {from_id} has {from_wallet.cash_balance:.2f}, needs {amount:.2f}")

    from_wallet.cash_balance -= amount
    to_wallet.cash_balance   += amount


# =========================================================
# LOCK COLLATERAL (WRITER)
# Moves funds from available cash → locked
# =========================================================
def lock_collateral(session, user_id: int, amount: float):
    wallet = get_wallet(session, user_id)

    available = wallet.cash_balance - wallet.locked_balance

    if available < amount:
        raise ValueError(f"Insufficient collateral: user {user_id} has {available:.2f} available, needs {amount:.2f}")

    wallet.locked_balance += amount


# =========================================================
# UNLOCK COLLATERAL (WRITER — on settlement or transfer)
# =========================================================
def unlock_collateral(session, user_id: int, amount: float):
    wallet = get_wallet(session, user_id)
    wallet.locked_balance = max(0.0, wallet.locked_balance - amount)
