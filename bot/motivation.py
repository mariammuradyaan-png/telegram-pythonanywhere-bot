"""Daily morning motivational broadcast — opt-in list + sender.

Subscribers are stored as a single JSON blob under one key so we can
enumerate them at broadcast time (the KV store has no scan/list op). Like
every other consumer of the store, this degrades gracefully: with no
store configured (stateless mode) subscribing is impossible and returns
False, and reads return an empty list.

The broadcast itself is triggered by an authenticated POST to
/api/broadcast (see api/index.py), which the GitHub Actions cron in
.github/workflows/motivation.yml hits every morning — PA's free tier has
no scheduler of its own.
"""

import json

from bot.clients import bot, store
from bot.config import MOTIVATION_PROMPT, SYSTEM_PROMPT
from bot.providers import generate

# One key holds {str(user_id): chat_id} for everyone opted in. No TTL —
# a subscription lasts until the user runs /unsubscribe (like provider
# preferences, unlike conversation history which expires).
_SUBS_KEY = "motivation:subscribers"


def _load_subs() -> dict:
    """Return the {str(user_id): chat_id} map. Empty dict on any failure."""
    if store is None:
        return {}
    try:
        raw = store.get(_SUBS_KEY)
    except Exception as e:
        print(f"Store read error (motivation): {e}")
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
        print(f"Store write error (motivation): {e}")
        return False


def is_subscribed(user_id: int) -> bool:
    return str(user_id) in _load_subs()


def subscribe(user_id: int, chat_id: int) -> bool:
    """Opt a user in, remembering which chat to message. Returns True on
    success, False if storage is unavailable or the write failed."""
    if store is None:
        return False
    subs = _load_subs()
    subs[str(user_id)] = chat_id
    return _save_subs(subs)


def unsubscribe(user_id: int) -> bool:
    """Opt a user out. Idempotent — removing an absent user still returns
    True. Returns False only if storage is unavailable / the write failed."""
    if store is None:
        return False
    subs = _load_subs()
    subs.pop(str(user_id), None)
    return _save_subs(subs)


def get_subscribers() -> dict:
    return _load_subs()


def build_motivation_message() -> str:
    """Generate one fresh morning message in Mariam's voice.

    user_id 0 is a sentinel with no stored provider preference, so this
    always dispatches to the main (OpenAI-compatible) provider — a
    broadcast has no single owning conversation, so it deliberately
    doesn't touch anyone's history.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": MOTIVATION_PROMPT},
    ]
    return generate(0, messages)


def broadcast_motivation() -> dict:
    """Send one motivational message to every subscriber.

    Returns {"total", "sent", "failed", "removed"}. The message is
    generated once and sent to everyone. Sends are best-effort: a failure
    to one user is logged and skipped so it can't stop the rest, and users
    who have blocked the bot (Telegram 403) are auto-unsubscribed so the
    list doesn't accumulate dead chats. No AI call is made when nobody is
    subscribed.
    """
    subs = _load_subs()
    result = {"total": len(subs), "sent": 0, "failed": 0, "removed": 0}
    if not subs:
        return result

    message = build_motivation_message()
    blocked = []
    for user_id, chat_id in subs.items():
        try:
            bot.send_message(chat_id, message)
            result["sent"] += 1
        except Exception as e:
            result["failed"] += 1
            print(f"Motivation send failed for user {user_id}: {e}")
            # error_code 403 = the user blocked the bot or deleted the
            # chat; they'll never receive messages again, so drop them.
            if getattr(e, "error_code", None) == 403:
                blocked.append(user_id)

    if blocked:
        # Re-read before pruning so we don't clobber a /subscribe that
        # landed mid-broadcast.
        current = _load_subs()
        for uid in blocked:
            current.pop(uid, None)
        _save_subs(current)
        result["removed"] = len(blocked)

    return result
