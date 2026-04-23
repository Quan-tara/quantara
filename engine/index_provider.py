import random
import time
import threading
from collections import deque

# =========================================================
# LATE DELIVERY RISK INDEX — 0 to 100
# =========================================================
# 0   = 0% probability of late delivery (perfect conditions)
# 100 = 100% probability of late delivery (guaranteed late)
#
# In real logistics, late delivery rates are 2–8%.
# Normal operating range: 5–20
# Elevated range:        20–50  (disruption)
# Crisis range:          50+    (major incident)
#
# MARKET PRICE FORMULA:
#   market_price = premium × (index / 100)
#
# At index=10: position worth 10% of premium (cheap insurance)
# At index=80: position worth 80% of premium (crisis premium)
# =========================================================

TICK_INTERVAL = 10       # seconds between ticks
HISTORY_SIZE  = 360      # store last 60 minutes of ticks (360 × 10s)

# =========================================================
# FACTOR DEFINITIONS
# Each factor: name, weight, baseline (normal value),
# current value, spike state
# =========================================================
FACTORS = [
    {
        "id":       "weather",
        "name":     "Weather Severity",
        "desc":     "Rain, wind, fog affecting delivery routes",
        "weight":   0.30,
        "baseline": 14.0,   # typical fine weather
        "value":    14.0,
        "spike":    None,   # None or {"remaining_ticks": N, "decay_per_tick": D}
    },
    {
        "id":       "traffic",
        "name":     "Traffic Congestion",
        "desc":     "Road conditions, accidents, rush hour",
        "weight":   0.25,
        "baseline": 16.0,
        "value":    10.0,
        "spike":    None,
    },
    {
        "id":       "driver",
        "name":     "Driver Availability",
        "desc":     "Fatigue, sickness, shortage of drivers",
        "weight":   0.20,
        "baseline": 11.0,
        "value":    6.0,
        "spike":    None,
    },
    {
        "id":       "route",
        "name":     "Route Complexity",
        "desc":     "Distance, stops, urban density",
        "weight":   0.15,
        "baseline": 13.0,
        "value":    9.0,
        "spike":    None,
    },
    {
        "id":       "volume",
        "name":     "Package Volume",
        "desc":     "Demand surge, warehouse overload",
        "weight":   0.10,
        "baseline": 12.0,
        "value":    7.0,
        "spike":    None,
    },
]

# Spike probability per tick per factor (0.8% = roughly 1 spike per ~20 min per factor)
SPIKE_PROBABILITY = 0.022

_lock         = threading.Lock()
_index        = 10.0          # starting index
_history      = deque(maxlen=HISTORY_SIZE)   # list of (timestamp, index_value)
_vol_history  = deque(maxlen=HISTORY_SIZE)   # volatility over time (σ per tick)
_spike_log    = deque(maxlen=50)             # recent spike events for display


# =========================================================
# COMPUTE INDEX FROM FACTORS
# =========================================================
def _compute_index() -> float:
    total = sum(f["value"] * f["weight"] for f in FACTORS)
    return round(min(100.0, max(0.0, total)), 2)


# =========================================================
# TICK — called every TICK_INTERVAL seconds
# =========================================================
def _tick():
    global _index

    with _lock:
        for f in FACTORS:

            # --- Handle active spike decay ---
            if f["spike"] is not None:
                decay = f["spike"]["decay_per_tick"]
                f["value"] = max(f["baseline"], f["value"] - decay)
                f["spike"]["remaining_ticks"] -= 1
                if f["spike"]["remaining_ticks"] <= 0:
                    f["spike"] = None
                    f["value"] = f["baseline"] + random.uniform(-1, 1)

            else:
                # --- Normal drift ---
                # Drift toward baseline with small random walk
                drift     = (f["baseline"] - f["value"]) * 0.03   # gentle pull back
                noise     = random.uniform(-2.5, 2.5)
                f["value"] = round(
                    max(0.0, min(100.0, f["value"] + drift + noise)), 2
                )

                # --- Random spike trigger ---
                if random.random() < SPIKE_PROBABILITY:
                    magnitude        = random.uniform(20, 55)
                    duration_ticks   = random.randint(6, 18)   # 1–3 minutes
                    decay_per_tick   = magnitude / duration_ticks

                    f["value"]      = min(100.0, f["value"] + magnitude)
                    f["spike"]      = {
                        "remaining_ticks": duration_ticks,
                        "decay_per_tick":  round(decay_per_tick, 2),
                        "magnitude":       round(magnitude, 1),
                    }

                    _spike_log.appendleft({
                        "time":    time.strftime("%H:%M:%S"),
                        "factor":  f["name"],
                        "jump":    round(magnitude, 1),
                    })

        _index = _compute_index()
        now_ts = time.time()
        _history.append((_index, now_ts))
        # Compute and store current volatility for the vol history chart
        recent_v = [v for v, _ in list(_history)[-360:]]
        if len(recent_v) >= 2:
            mean_v = sum(recent_v) / len(recent_v)
            var_v  = sum((v - mean_v) ** 2 for v in recent_v) / len(recent_v)
            cur_vol = round(var_v ** 0.5, 4)
        else:
            cur_vol = 0.0
        _vol_history.append(cur_vol)

        # Persist tick to DB (non-blocking best-effort)
        try:
            from db.database import SessionLocal
            from db.models import IndexTick
            session = SessionLocal()
            session.add(IndexTick(value=round(_index, 4), volatility=cur_vol, ts=now_ts))
            # Prune rows older than 70 minutes to keep table small
            cutoff = now_ts - 4200
            session.query(IndexTick).filter(IndexTick.ts < cutoff).delete()
            session.commit()
            session.close()
        except Exception:
            pass  # never let DB errors stop the index


# =========================================================
# BACKGROUND TICKER
# =========================================================
def _ticker_loop():
    while True:
        time.sleep(TICK_INTERVAL)
        _tick()


# ── Restore history from DB on startup ──
def _restore_history():
    """Load last 60 min of ticks from DB so history survives restarts."""
    try:
        from db.database import SessionLocal
        from db.models import IndexTick
        cutoff = time.time() - 3600  # last 60 min
        session = SessionLocal()
        rows = session.query(IndexTick).filter(
            IndexTick.ts >= cutoff
        ).order_by(IndexTick.ts.asc()).all()
        session.close()
        if rows:
            with _lock:
                for row in rows:
                    _history.append((row.value, row.ts))
                    _vol_history.append(row.volatility)
            print(f"📈 Restored {len(rows)} index ticks from DB")
        else:
            print("📈 No recent ticks in DB — starting fresh")
    except Exception as e:
        print(f"⚠️ Could not restore index history: {e}")

_restore_history()

_thread = threading.Thread(target=_ticker_loop, daemon=True)
_thread.start()


# =========================================================
# PUBLIC API
# =========================================================
def get_risk_index() -> float:
    """Current index value (0–100)."""
    with _lock:
        return _index


def get_mark_price() -> float:
    """Alias for compatibility."""
    return get_risk_index()


def get_index_snapshot() -> dict:
    """
    Full snapshot: index, factors, probability, history, spike log.
    Used by the API endpoint.
    """
    with _lock:
        idx = _index

        factors_out = []
        for f in FACTORS:
            factors_out.append({
                "id":       f["id"],
                "name":     f["name"],
                "desc":     f["desc"],
                "weight":   f["weight"],
                "weight_pct": round(f["weight"] * 100),
                "value":    round(f["value"], 1),
                "contribution": round(f["value"] * f["weight"], 2),
                "spiking":  f["spike"] is not None,
                "spike_remaining": f["spike"]["remaining_ticks"] if f["spike"] else 0,
            })

        # history: last 60 ticks (10 min) for compact display
        history_vals = [v for v, _ in list(_history)[-60:]]
        # history_full: all stored history (up to 360 ticks = 60 min)
        history_full_vals = [v for v, _ in list(_history)]

        # 1-hour stats
        all_vals = [v for v, _ in _history]
        h1_min   = round(min(all_vals), 1) if all_vals else idx
        h1_max   = round(max(all_vals), 1) if all_vals else idx
        h1_avg   = round(sum(all_vals) / len(all_vals), 1) if all_vals else idx

        # Probability buckets (based on last hour history)
        def pct_in_range(lo, hi):
            if not all_vals:
                return 0
            count = sum(1 for v in all_vals if lo <= v < hi)
            return round(count / len(all_vals) * 100)

        # Volatility = standard deviation of last 360 ticks (60 min)
        recent = [v for v, _ in list(_history)[-360:]]
        if len(recent) >= 2:
            mean_r = sum(recent) / len(recent)
            variance = sum((v - mean_r) ** 2 for v in recent) / len(recent)
            volatility = round(variance ** 0.5, 2)
        else:
            volatility = 0.0

        return {
            "index":       idx,
            "probability": round(idx, 1),          # index IS the probability
            "on_time_pct": round(100 - idx, 1),
            "factors":     factors_out,
            "history":     history_vals,
            "spike_log":   list(_spike_log)[:10],
            "stats": {
                "h1_min": h1_min,
                "h1_max": h1_max,
                "h1_avg": h1_avg,
            },
            "buckets": {
                "0_20":   pct_in_range(0,  20),
                "20_40":  pct_in_range(20, 40),
                "40_60":  pct_in_range(40, 60),
                "60_80":  pct_in_range(60, 80),
                "80_100": pct_in_range(80, 100),
            },
            "volatility": volatility,
            "history":      history_vals,            # last 10 min (kept for compat)
            "history_full": history_full_vals,       # full 60 min — used by all charts
            "vol_history":      list(_vol_history)[-60:],
            "vol_history_full": list(_vol_history),
        }


# =========================================================
# PUBLISHED RATE — daily late delivery rate simulation
# ─────────────────────────────────────────────────────────
# Simulates what a real data feed would provide.
# Published once per day at 00:00 UTC.
# rate = mean(index_last_24h)/100 + gaussian_noise(0, 0.04)
# expressed as a percentage, e.g. 18.3
# =========================================================
import random as _random
from datetime import datetime as _datetime, timedelta as _timedelta

_last_published_date = None   # date of last publication
_current_published_rate = None  # most recently published rate


def _compute_published_rate() -> float:
    """Simulate the published rate from the last 24h of index history."""
    with _lock:
        vals = [v for v, _ in list(_history)]
    if not vals:
        return 10.0
    base = sum(vals) / len(vals) / 100.0
    noise = _random.gauss(0, 0.04)
    rate = max(0.0, min(100.0, (base + noise) * 100))
    return round(rate, 1)


def get_current_published_rate() -> dict:
    """
    Returns the most recently published daily rate for today.
    Priority:
      1. In-memory cache (fastest, already computed this session)
      2. Database (survives restarts — today's rate already published)
      3. Compute fresh and save (first call of the day)
    """
    global _last_published_date, _current_published_rate

    today = _datetime.utcnow().date()

    # Already have today's rate in memory
    if _last_published_date == today and _current_published_rate is not None:
        pass  # use cached value
    else:
        # Try to load from database first (survives restarts)
        loaded = False
        try:
            from db.database import SessionLocal
            from db.models import PublishedRate
            session = SessionLocal()
            existing = session.query(PublishedRate).filter(
                PublishedRate.period_end >= _datetime.utcnow().replace(
                    hour=0, minute=0, second=0, microsecond=0)
            ).order_by(PublishedRate.created_at.desc()).first()
            session.close()
            if existing:
                _current_published_rate = existing.rate
                _last_published_date = today
                loaded = True
        except Exception:
            pass

        if not loaded:
            # No DB record for today — compute and persist
            _current_published_rate = _compute_published_rate()
            _last_published_date = today
            # Persist so restarts don't change the value
            try:
                from db.database import SessionLocal
                from db.models import PublishedRate
                session = SessionLocal()
                now = _datetime.utcnow()
                session.add(PublishedRate(
                    rate=_current_published_rate,
                    period_start=now - _timedelta(days=1),
                    period_end=now,
                ))
                session.commit()
                session.close()
            except Exception as e:
                print(f"⚠️ Could not persist published rate: {e}")

    return {
        "rate":       _current_published_rate,
        "threshold":  20.0,
        "in_the_money": _current_published_rate > 20.0,
        "date":       str(_last_published_date),
    }


def publish_daily_rate() -> dict:
    """
    Force-publish a new daily rate (called by the daily scheduler).
    Saves to DB and returns the result.
    """
    global _last_published_date, _current_published_rate

    rate = _compute_published_rate()
    _current_published_rate = rate
    _last_published_date = _datetime.utcnow().date()

    # Persist to DB
    try:
        from db.database import SessionLocal
        from db.models import PublishedRate
        session = SessionLocal()
        now = _datetime.utcnow()
        pr = PublishedRate(
            rate         = rate,
            period_start = now - _timedelta(days=1),
            period_end   = now,
        )
        session.add(pr)
        session.commit()
        session.close()
    except Exception as e:
        print(f"⚠️ Failed to persist published rate: {e}")

    return {"rate": rate, "date": str(_last_published_date)}


def get_running_estimate() -> float:
    """
    Live estimate of today's rate based on index so far.
    Not the official published rate — just a running indicator.
    """
    with _lock:
        vals = [v for v, _ in list(_history)]
    if not vals:
        return 10.0
    return round(sum(vals) / len(vals) / 100.0 * 100, 1)


# ── Daily publication scheduler ──
def _daily_publisher():
    """Publishes a new rate every 24 hours at midnight UTC."""
    while True:
        now = _datetime.utcnow()
        # Next midnight UTC
        tomorrow = (now + _timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sleep_secs = (tomorrow - now).total_seconds()
        time.sleep(sleep_secs)
        result = publish_daily_rate()
        print(f"📊 Daily rate published: {result['rate']}% ({result['date']})")


_daily_thread = threading.Thread(target=_daily_publisher, daemon=True)
_daily_thread.start()
print("📊 Daily rate publisher started")
