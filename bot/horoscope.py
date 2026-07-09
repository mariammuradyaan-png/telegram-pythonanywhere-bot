"""Daily horoscope — opt-in list (with each user's sign) + sender.

Registration captures the user's zodiac sign ONCE (from a sign name or a
birthday) and stores it, so we never have to ask again. The daily send
groups subscribers by sign and generates one reading per distinct sign
(≤12 AI calls total, not one per user), then delivers each person theirs.

Storage and graceful degradation mirror bot/motivation.py: one JSON blob
under a single key, empty/False fallbacks when no store is configured.
Triggered by an authenticated POST to /api/horoscope (see api/index.py),
hit by the GitHub Actions cron in .github/workflows/horoscope.yml.
"""

import json

from bot.clients import bot, store
from bot.config import HOROSCOPE_PROMPT, SYSTEM_PROMPT
from bot.providers import generate

# One key holds {str(user_id): {"chat_id": int, "sign": str}}. No TTL — a
# subscription lasts until /horoscope stop.
_SUBS_KEY = "horoscope:subscribers"

ZODIAC = (
    "aries",
    "taurus",
    "gemini",
    "cancer",
    "leo",
    "virgo",
    "libra",
    "scorpio",
    "sagittarius",
    "capricorn",
    "aquarius",
    "pisces",
)

# Last day (inclusive) of the *earlier* sign in each month. day <= cutoff
# → the sign whose slot is (month-1); otherwise the next sign (month).
# Index 0 = January. Verified against standard Western zodiac date ranges.
_CUTOFF = (19, 18, 20, 19, 20, 20, 22, 22, 22, 22, 21, 21)
_BY_MONTH = (
    "capricorn",  # Jan (before cutoff)
    "aquarius",  # Feb
    "pisces",  # Mar
    "aries",  # Apr
    "taurus",  # May
    "gemini",  # Jun
    "cancer",  # Jul
    "leo",  # Aug
    "virgo",  # Sep
    "libra",  # Oct
    "scorpio",  # Nov
    "sagittarius",  # Dec
    "capricorn",  # Dec after cutoff wraps back to Capricorn
)


def sign_from_date(month: int, day: int) -> str:
    """Return the zodiac sign for a (month, day). month 1-12, day 1-31."""
    idx = month - 1 if day <= _CUTOFF[month - 1] else month
    return _BY_MONTH[idx]


def parse_sign(text: str):
    """Parse a sign name or a birthday into a zodiac sign, or None.

    Accepts a sign name ("leo") or a date with month first — "MM-DD",
    "MM/DD", or "YYYY-MM-DD" (separators - / . all work). Only the first
    whitespace token is considered.
    """
    if not text:
        return None
    token = text.strip().split()[0].lower()
    if token in ZODIAC:
        return token
    parts = token.replace("/", "-").replace(".", "-").split("-")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3 and len(parts[0]) == 4:  # YYYY-MM-DD
        _, month, day = nums
    elif len(nums) == 2:  # MM-DD
        month, day = nums
    else:
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return sign_from_date(month, day)


def _load_subs() -> dict:
    if store is None:
        return {}
    try:
        raw = store.get(_SUBS_KEY)
    except Exception as e:
        print(f"Store read error (horoscope): {e}")
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_subs(subs: dict) -> bool:
    if store is None:
        return False
    try:
        store.set(_SUBS_KEY, json.dumps(subs))
        return True
    except Exception as e:
        print(f"Store write error (horoscope): {e}")
        return False


def get_subscriber(user_id: int):
    """Return {"chat_id", "sign"} for a user, or None if not registered."""
    return _load_subs().get(str(user_id))


def get_subscribers() -> dict:
    return _load_subs()


def subscribe(user_id: int, chat_id: int, sign: str) -> bool:
    """Register a user for daily horoscopes with their sign. Returns True
    on success, False if storage is unavailable / the write failed."""
    if store is None:
        return False
    subs = _load_subs()
    subs[str(user_id)] = {"chat_id": chat_id, "sign": sign}
    return _save_subs(subs)


def unsubscribe(user_id: int) -> bool:
    """Opt a user out. Idempotent — absent user still returns True."""
    if store is None:
        return False
    subs = _load_subs()
    subs.pop(str(user_id), None)
    return _save_subs(subs)


def build_horoscope_message(sign: str) -> str:
    """Generate today's reading for one sign, in Mariam's voice.

    user_id 0 is a sentinel with no stored provider preference, so this
    always dispatches to the main (OpenAI-compatible) provider.
    """
    prompt = HOROSCOPE_PROMPT.replace("{sign}", sign.title())
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    return generate(0, messages)


def broadcast_horoscopes() -> dict:
    """Send every subscriber their sign's reading.

    Generates ONE reading per distinct subscribed sign (not per user), so
    N subscribers across M signs cost M AI calls. Per-user send failures
    are logged and skipped; users who blocked the bot (403) are
    auto-unsubscribed. No AI call is made when nobody is subscribed.
    """
    subs = _load_subs()
    result = {"total": len(subs), "sent": 0, "failed": 0, "removed": 0}
    if not subs:
        return result

    signs = {
        info.get("sign") for info in subs.values() if info.get("sign") in ZODIAC
    }
    readings = {sign: build_horoscope_message(sign) for sign in signs}

    blocked = []
    for user_id, info in subs.items():
        sign = info.get("sign")
        chat_id = info.get("chat_id")
        reading = readings.get(sign)
        if reading is None or chat_id is None:
            result["failed"] += 1
            print(f"Horoscope skip for user {user_id}: bad record {info!r}")
            continue
        try:
            bot.send_message(chat_id, reading)
            result["sent"] += 1
        except Exception as e:
            result["failed"] += 1
            print(f"Horoscope send failed for user {user_id}: {e}")
            if getattr(e, "error_code", None) == 403:
                blocked.append(user_id)

    if blocked:
        current = _load_subs()
        for uid in blocked:
            current.pop(uid, None)
        _save_subs(current)
        result["removed"] = len(blocked)

    return result
