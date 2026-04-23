"""
Run once before first launch:
    python init_db.py

Creates all tables and seeds the 12 standardised contract series.
Safe to re-run — uses INSERT OR IGNORE for seed data.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.database import engine, SessionLocal, Base
from db.models import ContractSeries, Wallet
from engine.constants import MM_USER_ID


# ── Create all tables ──
Base.metadata.create_all(bind=engine)
print("✅ Tables created")

# ── Seed the 12 contract series ──
SERIES = [
    # id, collateral, expiry_mins, threshold, label
    (1,  100,    60,    20.0, "1h · €100 · >20%"),
    (2,  1000,   60,    20.0, "1h · €1,000 · >20%"),
    (3,  10000,  60,    20.0, "1h · €10,000 · >20%"),
    (4,  100,    1440,  20.0, "24h · €100 · >20%"),
    (5,  1000,   1440,  20.0, "24h · €1,000 · >20%"),
    (6,  10000,  1440,  20.0, "24h · €10,000 · >20%"),
    (7,  100,    4320,  20.0, "3d · €100 · >20%"),
    (8,  1000,   4320,  20.0, "3d · €1,000 · >20%"),
    (9,  10000,  4320,  20.0, "3d · €10,000 · >20%"),
    (10, 100,    10080, 20.0, "7d · €100 · >20%"),
    (11, 1000,   10080, 20.0, "7d · €1,000 · >20%"),
    (12, 10000,  10080, 20.0, "7d · €10,000 · >20%"),
]

session = SessionLocal()
try:
    for sid, col, exp, thr, lbl in SERIES:
        existing = session.query(ContractSeries).filter(ContractSeries.id == sid).first()
        if not existing:
            session.add(ContractSeries(
                id=sid, collateral=col, expiry_mins=exp,
                threshold=thr, label=lbl
            ))
    session.commit()
    print(f"✅ {len(SERIES)} contract series seeded")

    # Create MM wallet if missing
    mm_wallet = session.query(Wallet).filter(Wallet.user_id == MM_USER_ID).first()
    if not mm_wallet:
        session.add(Wallet(user_id=MM_USER_ID, cash_balance=10000.0, locked_balance=0.0))
        session.commit()
        print("✅ MM wallet created")
    else:
        print("✅ MM wallet already exists")

finally:
    session.close()

print("\n🚀 Database ready. You can now start the bot and web server.")
