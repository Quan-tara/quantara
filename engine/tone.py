"""
Headline tone — test phase (21 Nov 2026)
========================================
Quant news-sentiment feeds score how negative each article is. We do the same for story headlines,
with the AI we already use: up to BATCH new headlines per reading in ONE call, each scored once and
cached (headline_tones). Score: -1 = very bad for the brand named · 0 = neutral · +1 = good news.

Recorded only: the tone is stored on each warning, and the track record shows whether warnings with
a clearly negative headline (≤ NEGATIVE) are followed by damage more often than the rest.
"""

import json
import time

from db.models import HeadlineTone

BATCH = 20
NEGATIVE = -0.5

_PROMPT = """Score how each US food & drink news headline reads for the brand or company it is about.
-1 = very bad news for it (anger, harm, scandal), 0 = neutral or mixed, +1 = clearly good news.
Answer JSON only: {{"scores": [one number per headline, same order]}}
Headlines:
{lines}"""


def tones(session, titles):
    """{title: tone or None} — cached values, plus one AI call for up to BATCH new headlines."""
    titles = [t for t in dict.fromkeys(titles) if t]
    if not titles:
        return {}
    got = {r.title: r.tone for r in session.query(HeadlineTone).filter(HeadlineTone.title.in_([t[:300] for t in titles])).all()}
    out = {t: got.get(t[:300]) for t in titles}
    new = [t for t in titles if t[:300] not in got][:BATCH]
    if new:
        try:
            from engine.narratives import ask_json
            res = ask_json(_PROMPT.format(lines="\n".join(f"{i + 1}. {t[:200]}" for i, t in enumerate(new))),
                           max_tokens=40 + 8 * len(new))
        except Exception:
            res = None
        scores = (res or {}).get("scores")
        if isinstance(scores, list) and len(scores) == len(new):
            now = time.time()
            for t, v in zip(new, scores):
                try:
                    v = max(-1.0, min(1.0, float(v)))
                except Exception:
                    v = None
                session.add(HeadlineTone(title=t[:300], tone=v, ts=now))
                out[t] = v
            session.commit()
            print(f"🎨 tone: {len(new)} new headlines scored")
    return out


def label(v):
    if v is None:
        return "not scored yet"
    return "very negative" if v <= -0.7 else "negative" if v <= NEGATIVE else "neutral" if v < 0.3 else "positive"
