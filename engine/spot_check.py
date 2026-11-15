"""
Automatic check for spots — so nobody has to read every post before it goes live.

  1. rules (instant, always on)
       • a serious claim (mold, contamination, illness, fraud, abuse…) needs a source link
         or must say where it was seen ("Reddit users report…", "according to…")
       • no calls to action ("let's all post…", "email the CEO", "make it trend")
  2. AI check (Gemini, if GEMINI_API_KEY is set; a few seconds)
       • stated as fact without a source? about a private person? a call to action? abusive?
     If the AI can't be reached, the rules alone decide — a spot is never blocked by an outage.

check(reason, link, brand) → (ok, message for the player)
"""
import json
import os
import re

import requests

SERIOUS_RX = re.compile(
    r"\bmou?ld\w*|\bcontaminat\w*|\bpoison\w*|\bsick\b|\bsickness|\bill\b|\billness|\bvomit\w*|\bdiarrh?o?ea\b|"
    r"\bbacteria\w*|\bsalmonella|\be\.? ?coli\b|\blisteria|\bworms?\b|\binsects?\b|\bbugs?\b|\bcockroach\w*|\brats?\b|"
    r"\bmice\b|\bglass\b|\bmetal (?:piece|fragment|shard)s?\b|\btoxic\b|\bcancer\b|\bdied\b|\bdeath\b|\bhospital\w*|"
    r"\bfraud\w*|\bscam\w*|\blied\b|\blying\b|\bcover(?:ed)?[- ]?up\b|\bstole\b|\bsteal\w*|\btheft\b|\billegal\w*|"
    r"\babus\w*|\bharass\w*|\bracis\w*|\bdiscriminat\w*|\bassault\w*|\bunderpa(?:y|id)\b|\bwage theft\b", re.I)
ATTRIBUTION_RX = re.compile(
    r"\breport(?:s|ed|ing)?\b|\baccording to\b|\bclaim(?:s|ed|ing)?\b|\balleg\w*|\bsay(?:s|ing)?\b|\bsaid\b|"
    r"\bposts?\b|\bposted\b|\bvideos?\b|\bthread\b|\breddit\b|\btiktok\b|\bbluesky\b|\btwitter\b|\binstagram\b|"
    r"\byoutube\b|\breview(?:s|ers)?\b|\bcustomers? (?:are )?(?:say|complain|post)\w*|\bpeople (?:are )?(?:say|post|complain)\w*|"
    r"\brumou?rs?\b|\bwhispers?\b|\bunconfirmed\b", re.I)
ACTION_RX = re.compile(
    r"\blet'?s (?:all )?(?:post|tweet|share|spam|flood|email|call|boycott|report|go)\b|\beveryone (?:should )?(?:post|tweet|share|"
    r"email|call|boycott|go)\b|\bemail the ceo\b|\bcall (?:their|the) (?:hotline|ceo|office|store)\b|\bmake (?:it|this) trend\b|"
    r"\braid\b|\bspam\b|\bflood (?:their|the)\b|\bbrigad\w*|\bgo (?:post|comment|review)\b|\b1[- ]?star (?:them|reviews)\b|"
    r"\bleave (?:bad|1[- ]?star|one[- ]?star) reviews\b", re.I)

GEMINI = "https://generativelanguage.googleapis.com/v1beta"
_model = {"name": None}

PROMPT = """You check short posts on a game where players flag trouble brewing at US food brands before the news reports it.
Brand: {brand}
Post: "{reason}"
Source link given: {link}
Title of the linked page: {title}

Answer with JSON only: {{"verdict": "ok" | "needs_source" | "private_person" | "call_to_action" | "abusive", "why": "<max 15 words, to the player>"}}
- "needs_source": it states a serious accusation (health, safety, crime, mistreatment) as a FACT and gives no link and no sign of where it was seen.
- "unrelated_link": it makes a serious accusation and the link (judging by its address and page title) clearly does not report that problem — e.g. a shop or product page, a homepage, an unrelated article.
- "private_person": it names or targets a private individual (an employee, a customer), not the company or a public executive.
- "call_to_action": it asks others to post, complain, review-bomb, contact the company or otherwise act against the brand.
- "abusive": insults, hate, threats.
- "ok": everything else, including opinions, rumours clearly marked as such, and claims with a link. Be lenient: when unsure, "ok"."""


def _domain(link):
    from urllib.parse import urlparse
    try:
        d = urlparse(link or "").netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


def _own_site(link, brand):
    """Is the link the brand's own website (shop, product page)? That is never a source for a claim against it."""
    d = _domain(link)
    b = re.sub(r"[^a-z0-9]", "", (brand or "").lower())
    if not (d and b and len(b) >= 3):
        return False
    labels = [re.sub(r"[^a-z0-9]", "", x) for x in d.split(".")[:-1]]     # without the .com / .co
    return any(l == b for l in labels)


def page_title(link):
    """The linked page's title (first 60 KB, 5 s max) — so the AI can judge whether it supports the claim."""
    if not link:
        return ""
    try:
        with requests.get(link, timeout=5, stream=True, headers={"User-Agent": "Mozilla/5.0 QuantaraBot/1.0"}) as r:
            raw = r.raw.read(60000, decode_content=True) or b""
        m = re.search(rb"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
        if not m:
            m = re.search(rb'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', raw, re.I)
        import html
        return html.unescape(m.group(1).decode("utf-8", "ignore")).strip()[:200] if m else ""
    except Exception:
        return ""


def _rules(reason, link, brand=None):
    if ACTION_RX.search(reason):
        return False, ("Spots are for calling what will happen, not for making it happen. "
                       "Please remove the call to action (no campaigns, posting drives or contacting the brand).")
    if SERIOUS_RX.search(reason) and not link and not ATTRIBUTION_RX.search(reason):
        return False, ("That's a serious claim. Add a source link, or say where you saw it "
                       "(for example: \"Reddit users report…\", \"a TikTok video shows…\").")
    if SERIOUS_RX.search(reason) and link and _own_site(link, brand):
        return False, (f"A link to {brand}'s own website isn't a source for this claim. "
                       "Link to where the problem is reported (news, a post, a video), or say where you saw it.")
    return True, ""


def _ai(reason, link, brand, title=""):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    known = [os.getenv("GEMINI_MODEL"), _model["name"]]
    try:
        from engine.titles import _state
        known.append(_state.get("model"))          # the model the short-title job already found
    except Exception:
        pass
    models = [m for m in known if m]
    if not models:                                  # first use: ask Google once, then remember
        if "list" not in _model:
            try:
                from engine.titles import _gemini_models
                _model["list"] = _gemini_models(key)[:3]
            except Exception:
                _model["list"] = []
        models = _model["list"]
    if not models:
        return None
    prompt = PROMPT.format(brand=brand or "?", reason=reason.replace('"', "'")[:600], link=link or "none",
                           title=(title or "unknown").replace('"', "'"))
    for m in dict.fromkeys(x for x in models if x):
        try:
            r = requests.post(f"{GEMINI}/models/{m}:generateContent", params={"key": key}, timeout=8,
                              json={"contents": [{"parts": [{"text": prompt}]}],
                                    "generationConfig": {"temperature": 0, "maxOutputTokens": 120,
                                                         "responseMimeType": "application/json"}})
        except Exception:
            return None
        if r.status_code in (400, 404) and "not found" in r.text.lower():
            continue
        if r.status_code != 200:
            return None
        _model["name"] = m
        try:
            parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}]
            txt = "".join(p.get("text", "") for p in parts)
            m_ = re.search(r"\{.*\}", txt, re.S)
            return json.loads(m_.group(0)) if m_ else None
        except Exception:
            return None
    return None


MESSAGES = {
    "needs_source": "That reads as a serious claim stated as fact. Add a source link, or say where you saw it.",
    "private_person": "Please don't name or target private people (staff, customers). Talk about the company.",
    "call_to_action": "Spots are for calling what will happen, not for making it happen. Please remove the call to action.",
    "abusive": "Please keep it civil. Insults, hate and threats aren't allowed.",
    "unrelated_link": "Your link doesn't seem to report this problem. Link to where it's reported, or say where you saw it.",
}


def check(reason, link=None, brand=None):
    """(ok, message). Rules first, then the AI. An AI outage never blocks a spot."""
    reason = (reason or "").strip()
    ok, msg = _rules(reason, link, brand)
    if not ok:
        return False, msg
    title = page_title(link) if (link and SERIOUS_RX.search(reason)) else ""
    v = _ai(reason, link, brand, title)
    if not v:
        return True, ""
    verdict = str(v.get("verdict", "ok")).lower()
    if verdict == "needs_source" and link:
        return True, ""                                # a link was given — that's the rule
    if verdict in MESSAGES:
        why = str(v.get("why") or "").strip()
        return False, MESSAGES[verdict] + (f" ({why})" if why and len(why) < 120 else "")
    return True, ""
