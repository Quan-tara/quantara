"""Reading old story lists without pulling every one of them out of the database.

The replays (tip calibration, preview, the history test) only use one reading every few hours, but used to
fetch every reading (each one a large JSON list) and skip most of them in Python. The database bills
that traffic (Supabase egress). Here the timestamps come first (tiny), then only the chosen readings.
"""
from db.models import ArenaReading


def sampled(session, arenas=None, since=0.0, every_s=3 * 3600, batch=40):
    """Yields (ts, arena, story_index) for one reading per arena every `every_s`, oldest first."""
    q = session.query(ArenaReading.id, ArenaReading.ts, ArenaReading.arena).filter(ArenaReading.ts >= since)
    if arenas:
        q = q.filter(ArenaReading.arena.in_(list(arenas)))
    last, ids = {}, []
    for rid, ts, arena in q.order_by(ArenaReading.ts).all():
        if ts - last.get(arena, -1e18) >= every_s:
            last[arena] = ts
            ids.append(rid)
    for i in range(0, len(ids), batch):
        part = ids[i:i + batch]
        rows = (session.query(ArenaReading.ts, ArenaReading.arena, ArenaReading.story_index)
                .filter(ArenaReading.id.in_(part)).order_by(ArenaReading.ts).all())
        for r in rows:
            yield r[0], r[1], r[2]
