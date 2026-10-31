"""
FSNI contracts — pricing, marks and settlement data
====================================================
Contracts and series carry an `underlying` field:
  "DP"   — Delivery Pulse (default; existing behaviour, untouched)
  "FSNI" — Food Sector Narrative Index (pilot)

FSNI contract: pays YES if the AVERAGE FSNI over the contract window is
above the threshold (default 30).

Model fair probability (a reference for the market maker — traders set the
real price, and the market-implied probability is what we want to observe):

  The FSNI is baseline-relative, so on normal days it sits near 20 and shocks
  fade. For the part of the window still to come:
      expected future mean  m = 20 + w · (current − 20)
      w = (τ / T_rem) · (1 − e^(−T_rem/τ)),   τ = 72 h (decay of a shock)
  The part already elapsed is known exactly (mean of stored readings).
      expected window mean  E = (elapsed_mean · T_el + m · T_rem) / T_total
      uncertainty           σ = 6 · (T_rem / T_total)
      P(YES) = Φ((E − threshold) / σ), clamped to [2 %, 98 %] while running.
"""

import math
import time
import calendar
from datetime import datetime

from db.database import SessionLocal

DP   = "DP"
FSNI = "FSNI"

FSNI_LONG_RUN   = 20.0     # where a baseline-relative FSNI sits on normal days
FSNI_DECAY_H    = 72.0     # τ — how fast a shock fades
FSNI_SIGMA      = 6.0      # uncertainty of the future part of the window mean
FSNI_SPREAD     = 0.25     # MM quotes ±25 % around model mid — traders set the price
FSNI_MIN_TICKS  = 6        # readings needed in a window to settle
_FSNI_ROWS_SQL  = "headlines IS NOT NULL AND headlines LIKE :fsni_pat"
_FSNI_PAT       = '%"raw":%'      # FSNI readings made with the baseline-relative scoring

_cache = {"ts": 0.0, "value": None}


def underlying_of(obj) -> str:
    u = getattr(obj, "underlying", None)
    return (u or DP).upper()


def is_fsni(obj) -> bool:
    return underlying_of(obj) == FSNI


def _ts(dt):
    """Naive-UTC datetime → unix seconds."""
    return float(calendar.timegm(dt.timetuple())) if dt else None


def fsni_current(session=None) -> float:
    """Latest FSNI reading (baseline-relative scoring). Cached 30 s."""
    now = time.time()
    if _cache["value"] is not None and now - _cache["ts"] < 30:
        return _cache["value"]
    import sqlalchemy as sa
    own = session is None
    session = session or SessionLocal()
    try:
        row = session.execute(sa.text(
            f"SELECT value FROM index_ticks WHERE {_FSNI_ROWS_SQL} ORDER BY ts DESC LIMIT 1"
        ), {"fsni_pat": _FSNI_PAT}).fetchone()
        val = float(row[0]) if row else FSNI_LONG_RUN
    except Exception as e:
        print(f"⚠️ fsni_current error: {e}")
        val = _cache["value"] if _cache["value"] is not None else FSNI_LONG_RUN
    finally:
        if own:
            session.close()
    _cache.update(ts=now, value=val)
    return val


def fsni_window_mean(start_ts: float, end_ts: float, session=None):
    """(mean, count) of FSNI readings in [start_ts, end_ts]. (None, 0) if none."""
    import sqlalchemy as sa
    own = session is None
    session = session or SessionLocal()
    try:
        row = session.execute(sa.text(
            f"SELECT AVG(value), COUNT(*) FROM index_ticks "
            f"WHERE ts >= :a AND ts <= :b AND {_FSNI_ROWS_SQL}"
        ), {"a": start_ts, "b": end_ts, "fsni_pat": _FSNI_PAT}).fetchone()
        if row and row[1]:
            return float(row[0]), int(row[1])
        return None, 0
    except Exception as e:
        print(f"⚠️ fsni_window_mean error: {e}")
        return None, 0
    finally:
        if own:
            session.close()


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def fair_prob(current, threshold, hours_total, hours_remaining,
              elapsed_mean=None) -> float:
    T  = max(hours_total, 1e-6)
    Tr = max(0.0, min(hours_remaining, T))
    Te = T - Tr
    if Tr <= 0:
        em = elapsed_mean if elapsed_mean is not None else current
        return 1.0 if em > threshold else 0.0
    w = (FSNI_DECAY_H / Tr) * (1 - math.exp(-Tr / FSNI_DECAY_H))
    m_future = FSNI_LONG_RUN + w * (current - FSNI_LONG_RUN)
    em = elapsed_mean if (elapsed_mean is not None and Te > 0) else m_future
    expected = (em * Te + m_future * Tr) / T
    sigma = max(0.5, FSNI_SIGMA * Tr / T)
    p = _phi((expected - threshold) / sigma)
    return max(0.02, min(0.98, p))


def contract_state(contract, session=None) -> dict:
    """Current FSNI, elapsed-window mean and hours for an active contract."""
    now = datetime.utcnow()
    created, expires = contract.created_at, contract.expires_at
    total_h = max(1e-6, (expires - created).total_seconds() / 3600) if created and expires else 168
    rem_h = max(0.0, (expires - now).total_seconds() / 3600) if expires else total_h
    mean, n = (None, 0)
    if created:
        mean, n = fsni_window_mean(_ts(created), _ts(min(now, expires or now)), session)
    return {"current": fsni_current(session), "elapsed_mean": mean, "elapsed_n": n,
            "hours_total": total_h, "hours_remaining": rem_h}


def fair_price(collateral, threshold, hours_total, hours_remaining,
               elapsed_mean=None, current=None, session=None) -> dict:
    cur = fsni_current(session) if current is None else current
    p = fair_prob(cur, threshold, hours_total, hours_remaining, elapsed_mean)
    mid = round(collateral * p, 2)
    return {
        "mid": mid,
        "bid": round(mid * (1 - FSNI_SPREAD), 2),
        "ask": round(mid * (1 + FSNI_SPREAD), 2),
        "tf": 1.0,
        "idx": round(cur, 2),
        "hrs_remaining": round(hours_remaining, 1),
        "prob": round(p * 100, 1),
        "running_avg": round(elapsed_mean, 2) if elapsed_mean is not None else None,
    }


def series_price(series, active_contract=None, session=None) -> dict:
    thr = series.threshold or 30.0
    if active_contract is not None:
        st = contract_state(active_contract, session)
        return fair_price(series.collateral, thr, st["hours_total"], st["hours_remaining"],
                          st["elapsed_mean"], st["current"], session)
    h = series.expiry_mins / 60
    return fair_price(series.collateral, thr, h, h, None, None, session)


def contract_mark(contract, collateral, session=None) -> float:
    """Fair value of one FSNI position (per unit)."""
    st = contract_state(contract, session)
    return fair_price(collateral, contract.settlement_threshold or 30.0,
                      st["hours_total"], st["hours_remaining"],
                      st["elapsed_mean"], st["current"], session)["mid"]


def settlement_rate(contract, session=None):
    """(rate, description) for an expired FSNI contract, or (None, reason)
    if there aren't enough readings yet — the caller should retry later."""
    mean, n = fsni_window_mean(_ts(contract.created_at), _ts(contract.expires_at), session)
    if n < FSNI_MIN_TICKS or mean is None:
        return None, f"only {n} FSNI readings in window (need {FSNI_MIN_TICKS})"
    hrs = (contract.expires_at - contract.created_at).total_seconds() / 3600
    return round(mean, 2), f"mean of {n} FSNI readings over {hrs:.0f}h window"
