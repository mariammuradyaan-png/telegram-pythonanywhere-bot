import os
from datetime import datetime
from bot.clients import bot, BOT_INFO, store
from bot.config import COMMIT_SHA, HF_SPACE_ID, HOSTING_LABEL, MODEL, RATE_LIMIT
from bot.ai import ask_ai
from bot.helpers import is_allowed, keep_typing, send_reply, should_respond
from bot.imagine import build_image_url
from bot.history import clear_history
from bot.motivation import subscribe, unsubscribe
from bot.preferences import get_provider, set_provider
from bot.rate_limit import is_rate_limited

# Verbose console logging for local dev and teaching. Enabled by
# BOT_VERBOSE_LOG=1 (run_local.py sets this automatically). Prints one
# line per inbound/outbound message so kids and teachers can see the
# conversation flow in their terminal while the bot is running.
VERBOSE_LOG = os.environ.get("BOT_VERBOSE_LOG", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _log(message, direction: str, text: str) -> None:
    """Print a one-line trace of a message in verbose mode.

    direction is "in" (user → bot) or "out" (bot → user). Text is
    truncated to 500 characters so long AI replies don't flood the
    terminal. Newlines are collapsed for single-line readability.
    """
    if not VERBOSE_LOG:
        return
    user = message.from_user
    user_name = (
        f"@{user.username}" if user.username else (user.first_name or f"user:{user.id}")
    )
    bot_name = f"@{BOT_INFO.username}"
    snippet = (text or "").replace("\n", " ").replace("\r", " ")
    if len(snippet) > 500:
        snippet = snippet[:500] + "..."
    if direction == "in":
        sender, receiver = user_name, bot_name
    else:
        sender, receiver = bot_name, user_name
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {sender} → {receiver}: {snippet}", flush=True)


def _command_lines():
    """The command menu, shared by /start and /help so they never drift.

    /model is only listed when a HF space is configured (that's the only
    condition under which the command is registered)."""
    lines = [
        "/help — show this message",
        "/joke — hear a joke in my voice",
        "/imagine <description> — I'll make you a romantic image",
        "/subscribe — get a good-morning message from me each day ☀️",
        "/unsubscribe — stop the daily messages",
        "/reset — clear our conversation and start fresh",
        "/about — a little about me",
        "/sha — show the live git commit SHA",
    ]
    if HF_SPACE_ID:
        lines.append("/model — switch AI provider")
    return lines


@bot.message_handler(commands=["start"], func=is_allowed)
def cmd_start(message):
    intro = (
        "Hi, I'm Mariam 💖 So happy you're here.\n"
        "Just talk to me like you'd talk to a friend — tell me about your day, "
        "or try one of these whenever you like:\n\n" + "\n".join(_command_lines())
    )
    bot.send_message(message.chat.id, intro)

@bot.message_handler(commands=["joke"], func=is_allowed)
def cmd_joke(message):
    # Routes through ask_ai so the joke comes out in Mariam's voice (the
    # system prompt is applied) and lands in her conversation memory like
    # any other turn. Same keep_typing / send_reply / error handling as
    # handle_message so a slow or failed generation behaves consistently.
    prompt = "Tell me a joke — something in your voice, short and playful."
    try:
        with keep_typing(message.chat.id):
            reply = ask_ai(message.from_user.id, prompt)
        send_reply(message, reply)
        _log(message, "out", reply)
    except Exception as e:
        print(f"Error in cmd_joke: {e}")
        bot.send_message(message.chat.id, "Something went wrong. Please try again.")


@bot.message_handler(commands=["imagine"], func=is_allowed)
def cmd_imagine(message):
    # Turn a description into a romantic-style image. We only build a
    # Pollinations URL and let Telegram fetch it (sendPhoto with a URL) —
    # so no outbound call is made from PA and no image API key is needed.
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.send_message(
            message.chat.id,
            "Tell me what to imagine 💖 — like:\n"
            "/imagine two cups of coffee by a rainy window",
        )
        return
    description = parts[1].strip()
    _log(message, "in", f"/imagine {description}")
    seed = getattr(message, "message_id", None)
    url = build_image_url(description, seed=seed)
    try:
        # upload_photo shows the "sending photo…" status while Telegram
        # pulls the image (Pollinations can take 10-30s to generate).
        bot.send_chat_action(message.chat.id, "upload_photo")
        bot.send_photo(message.chat.id, url, caption=f"✨ {description}")
        _log(message, "out", f"[image] {url}")
    except Exception as e:
        # Telegram couldn't fetch/generate in time — fall back to the raw
        # link so the user can still open it in a browser.
        print(f"Error in cmd_imagine: {e}")
        bot.send_message(
            message.chat.id,
            f"Couldn't send the image just now 💔 but here's the link:\n{url}",
        )
        _log(message, "out", f"[image error] {e}")


@bot.message_handler(commands=["help"], func=is_allowed)
def cmd_help(message):
    bot.send_message(message.chat.id, "\n".join(_command_lines()))


@bot.message_handler(commands=["subscribe"], func=is_allowed)
def cmd_subscribe(message):
    # Opting in needs the store — that's where the subscriber list lives.
    if store is None:
        bot.send_message(
            message.chat.id,
            "I can't set that up right now — daily messages need storage "
            "configured on my end 💔",
        )
        return
    if subscribe(message.from_user.id, message.chat.id):
        bot.send_message(
            message.chat.id,
            "You're in ⭐ I'll send you a little good-morning message every "
            "day. Say /unsubscribe anytime to stop.",
        )
    else:
        bot.send_message(
            message.chat.id, "Couldn't save that just now 💔 try again in a bit."
        )


@bot.message_handler(commands=["unsubscribe"], func=is_allowed)
def cmd_unsubscribe(message):
    if store is None:
        bot.send_message(
            message.chat.id, "Nothing to stop — daily messages aren't set up here."
        )
        return
    if unsubscribe(message.from_user.id):
        bot.send_message(
            message.chat.id,
            "Done — no more morning messages 💖 I'm still right here whenever "
            "you want to talk.",
        )
    else:
        bot.send_message(
            message.chat.id, "Couldn't update that just now 💔 try again in a bit."
        )


@bot.message_handler(commands=["reset"], func=is_allowed)
def cmd_reset(message):
    clear_history(message.from_user.id)
    bot.send_message(message.chat.id, "Conversation cleared. Starting fresh!")


@bot.message_handler(commands=["about"], func=is_allowed)
def cmd_about(message):
    if HF_SPACE_ID:
        provider = get_provider(message.from_user.id)
        model_line = f"{MODEL} (main)" if provider == "main" else f"{HF_SPACE_ID} (hf)"
    else:
        model_line = MODEL
    storage_line = "SQLite" if store is not None else "stateless (no memory)"
    lines = [
        f"Model  : {model_line}",
        f"Storage: {storage_line}",
        f"Hosting: {HOSTING_LABEL}",
    ]
    if COMMIT_SHA:
        lines.append(f"Version: {COMMIT_SHA}")
    bot.send_message(message.chat.id, "\n".join(lines))


@bot.message_handler(commands=["sha"], func=is_allowed)
def cmd_sha(message):
    sha = COMMIT_SHA or "unknown"
    bot.send_message(message.chat.id, f"Live SHA: {sha}")


if HF_SPACE_ID:

    @bot.message_handler(commands=["model"], func=is_allowed)
    def cmd_model(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            current = get_provider(message.from_user.id)
            bot.send_message(
                message.chat.id,
                f"Current provider: {current}\n\n"
                "Options:\n"
                "/model main — Cerebras (fast, multilingual, with memory)\n"
                "/model hf — ArmGPT (Armenian only, slow, no memory)",
            )
            return
        choice = parts[1].strip().lower()
        if choice not in ("main", "hf"):
            bot.send_message(
                message.chat.id, "Invalid choice. Use: /model main or /model hf"
            )
            return
        if not set_provider(message.from_user.id, choice):
            bot.send_message(
                message.chat.id, "Could not save preference. Try again later."
            )
            return
        if choice == "hf":
            bot.send_message(
                message.chat.id,
                "Switched to hf (ArmGPT).\n\n"
                "Note: this is a tiny base completion model trained only on Armenian text. "
                "It will continue whatever you write rather than answer questions, "
                "and it does not understand English. Replies take ~30-60s and there is no memory.",
            )
        else:
            bot.send_message(message.chat.id, "Switched to Main Provider.")


@bot.message_handler(content_types=["text"], func=is_allowed)
def handle_message(message):
    if not should_respond(message):
        return
    text = (message.text or "").replace(f"@{BOT_INFO.username}", "").strip()
    if not text:
        # Edited messages, forwards, or stickers-with-empty-caption can
        # arrive with no usable text. Don't burn rate-limit / AI calls on them.
        return
    _log(message, "in", text)
    if is_rate_limited(message.from_user.id):
        limit_msg = f"You've reached the daily limit of {RATE_LIMIT} messages. Try again tomorrow."
        bot.send_message(message.chat.id, limit_msg)
        _log(message, "out", f"[rate limited] {limit_msg}")
        return
    try:
        with keep_typing(message.chat.id):
            reply = ask_ai(message.from_user.id, text)
        send_reply(message, reply)
        _log(message, "out", reply)
    except Exception as e:
        print(f"Error in handle_message: {e}")
        bot.send_message(message.chat.id, "Something went wrong. Please try again.")
        _log(message, "out", f"[error] {e}")
