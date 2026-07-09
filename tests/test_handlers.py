from unittest.mock import patch, MagicMock


def make_message(text="hello", user_id=123, chat_id=456, chat_type="private"):
    msg = MagicMock()
    msg.text = text
    msg.from_user.id = user_id
    msg.chat.id = chat_id
    msg.chat.type = chat_type
    msg.reply_to_message = None
    return msg


HANDLER_PATCHES = {
    "bot.handlers.should_respond": True,
    "bot.handlers.is_rate_limited": False,
    "bot.handlers.BOT_INFO": MagicMock(id=42, username="testbot"),
}


def test_handle_message_calls_ask_ai():
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai", return_value="AI reply") as mock_ask,
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import handle_message

        msg = make_message(text="hello")
        handle_message(msg)
        mock_ask.assert_called_once_with(123, "hello")
        mock_send.assert_called_once_with(msg, "AI reply")


def test_handle_message_skips_when_not_responding():
    with (
        patch("bot.handlers.should_respond", return_value=False),
        patch("bot.handlers.ask_ai") as mock_ask,
    ):
        from bot.handlers import handle_message

        handle_message(make_message())
        mock_ask.assert_not_called()


def test_handle_message_rate_limited():
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=True),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import handle_message

        handle_message(make_message())
        mock_ask.assert_not_called()
        mock_bot.send_message.assert_called_once()
        assert "daily limit" in mock_bot.send_message.call_args[0][1]


def test_handle_message_sends_generic_error():
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai", side_effect=Exception("API key invalid")),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import handle_message

        handle_message(make_message())
        error_msg = mock_bot.send_message.call_args[0][1]
        assert "Something went wrong" in error_msg
        assert "API key" not in error_msg


def test_handle_message_none_text_skipped():
    """Stickers/photos/edits arriving with text=None must NOT call ask_ai
    (would burn rate limit and AI quota for no reason)."""
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import handle_message

        msg = make_message()
        msg.text = None
        handle_message(msg)
        mock_ask.assert_not_called()
        mock_send.assert_not_called()


def test_handle_message_mention_only_skipped():
    """In a group, '@testbot' alone strips to empty — don't call ask_ai."""
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.send_reply"),
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import handle_message

        msg = make_message(text="@testbot")
        handle_message(msg)
        mock_ask.assert_not_called()


# ── /joke ─────────────────────────────────────────────────────────────────────


def test_cmd_joke_routes_through_ask_ai():
    """/joke should generate via ask_ai (so the persona system prompt is
    applied) and reply through send_reply."""
    with (
        patch("bot.handlers.ask_ai", return_value="haha here's one ⚡") as mock_ask,
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.keep_typing") as mock_keep,
        patch("bot.handlers.bot"),
    ):
        mock_keep.return_value.__enter__ = MagicMock(return_value=None)
        mock_keep.return_value.__exit__ = MagicMock(return_value=None)
        from bot.handlers import cmd_joke

        msg = make_message(text="/joke")
        cmd_joke(msg)
        assert mock_ask.call_args[0][0] == 123
        mock_send.assert_called_once_with(msg, "haha here's one ⚡")
        mock_keep.assert_called_once_with(456)


def test_cmd_joke_sends_generic_error_on_failure():
    """A failed generation must not leak the exception to the user."""
    with (
        patch("bot.handlers.ask_ai", side_effect=Exception("API key invalid")),
        patch("bot.handlers.keep_typing") as mock_keep,
        patch("bot.handlers.bot") as mock_bot,
    ):
        mock_keep.return_value.__enter__ = MagicMock(return_value=None)
        mock_keep.return_value.__exit__ = MagicMock(return_value=None)
        from bot.handlers import cmd_joke

        cmd_joke(make_message(text="/joke"))
        error_msg = mock_bot.send_message.call_args[0][1]
        assert "Something went wrong" in error_msg
        assert "API key" not in error_msg


# ── /start & /help ─────────────────────────────────────────────────────────────


def test_cmd_start_introduces_and_lists_commands():
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.HF_SPACE_ID", ""),
    ):
        from bot.handlers import cmd_start

        cmd_start(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "Mariam" in sent
        for cmd in ("/joke", "/imagine", "/subscribe", "/reset", "/about"):
            assert cmd in sent


def test_cmd_help_lists_model_only_when_hf_enabled():
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.HF_SPACE_ID", ""),
    ):
        from bot.handlers import cmd_help

        cmd_help(make_message())
        assert "/model" not in mock_bot.send_message.call_args[0][1]

    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.HF_SPACE_ID", "some/space"),
    ):
        from bot.handlers import cmd_help

        cmd_help(make_message())
        assert "/model" in mock_bot.send_message.call_args[0][1]


# ── /subscribe & /unsubscribe ───────────────────────────────────────────────────


def test_cmd_subscribe_opts_user_in():
    with (
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.subscribe", return_value=True) as mock_sub,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_subscribe

        cmd_subscribe(make_message(text="/subscribe"))
        mock_sub.assert_called_once_with(123, 456)  # user_id, chat_id
        assert "day" in mock_bot.send_message.call_args[0][1].lower()


def test_cmd_subscribe_explains_when_stateless():
    with (
        patch("bot.handlers.store", None),
        patch("bot.handlers.subscribe") as mock_sub,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_subscribe

        cmd_subscribe(make_message(text="/subscribe"))
        mock_sub.assert_not_called()
        mock_bot.send_message.assert_called_once()


def test_cmd_subscribe_reports_write_failure():
    with (
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.subscribe", return_value=False),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_subscribe

        cmd_subscribe(make_message(text="/subscribe"))
        assert "try again" in mock_bot.send_message.call_args[0][1].lower()


def test_cmd_unsubscribe_opts_user_out():
    with (
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.unsubscribe", return_value=True) as mock_unsub,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_unsubscribe

        cmd_unsubscribe(make_message(text="/unsubscribe"))
        mock_unsub.assert_called_once_with(123)
        assert mock_bot.send_message.called


# ── /horoscope ──────────────────────────────────────────────────────────────────


def test_cmd_horoscope_registers_with_sign_and_sends_today():
    with (
        patch("bot.handlers.store", MagicMock()),
        patch("bot.horoscope.parse_sign", return_value="leo") as mock_parse,
        patch("bot.horoscope.subscribe", return_value=True) as mock_sub,
        patch("bot.handlers._send_todays_horoscope") as mock_today,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_horoscope

        cmd_horoscope(make_message(text="/horoscope leo"))
        mock_parse.assert_called_once_with("leo")
        mock_sub.assert_called_once_with(123, 456, "leo")
        mock_today.assert_called_once()  # first reading delivered immediately
        assert "Leo" in mock_bot.send_message.call_args[0][1]


def test_cmd_horoscope_asks_once_when_unregistered_and_no_arg():
    with (
        patch("bot.handlers.store", MagicMock()),
        patch("bot.horoscope.get_subscriber", return_value=None),
        patch("bot.handlers._send_todays_horoscope") as mock_today,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_horoscope

        cmd_horoscope(make_message(text="/horoscope"))
        mock_today.assert_not_called()
        assert "birthday" in mock_bot.send_message.call_args[0][1].lower()


def test_cmd_horoscope_registered_no_arg_reads_without_reasking():
    with (
        patch("bot.handlers.store", MagicMock()),
        patch(
            "bot.horoscope.get_subscriber",
            return_value={"chat_id": 456, "sign": "leo"},
        ),
        patch("bot.handlers._send_todays_horoscope") as mock_today,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import cmd_horoscope

        cmd_horoscope(make_message(text="/horoscope"))
        mock_today.assert_called_once()
        assert mock_today.call_args[0][1] == "leo"  # uses stored sign


def test_cmd_horoscope_stop_unsubscribes():
    with (
        patch("bot.handlers.store", MagicMock()),
        patch("bot.horoscope.unsubscribe", return_value=True) as mock_unsub,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_horoscope

        cmd_horoscope(make_message(text="/horoscope stop"))
        mock_unsub.assert_called_once_with(123)
        assert mock_bot.send_message.called


def test_cmd_horoscope_rejects_unparseable_sign():
    with (
        patch("bot.handlers.store", MagicMock()),
        patch("bot.horoscope.parse_sign", return_value=None),
        patch("bot.horoscope.subscribe") as mock_sub,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_horoscope

        cmd_horoscope(make_message(text="/horoscope banana"))
        mock_sub.assert_not_called()
        assert "couldn't read" in mock_bot.send_message.call_args[0][1].lower()


def test_cmd_horoscope_stateless_explains():
    with (
        patch("bot.handlers.store", None),
        patch("bot.horoscope.subscribe") as mock_sub,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_horoscope

        cmd_horoscope(make_message(text="/horoscope leo"))
        mock_sub.assert_not_called()
        mock_bot.send_message.assert_called_once()


# ── /imagine ──────────────────────────────────────────────────────────────────


def test_cmd_imagine_sends_photo_with_built_url():
    with (
        patch("bot.handlers.build_image_url", return_value="http://img/x") as mock_build,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_imagine

        msg = make_message(text="/imagine a rose")
        msg.message_id = 7
        cmd_imagine(msg)
        mock_build.assert_called_once_with("a rose", seed=7)
        mock_bot.send_photo.assert_called_once()
        args, kwargs = mock_bot.send_photo.call_args
        assert args[0] == 456  # chat id
        assert args[1] == "http://img/x"
        assert "a rose" in kwargs["caption"]


def test_cmd_imagine_prompts_when_no_description():
    with (
        patch("bot.handlers.build_image_url") as mock_build,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_imagine

        cmd_imagine(make_message(text="/imagine"))
        mock_build.assert_not_called()
        mock_bot.send_photo.assert_not_called()
        assert "imagine" in mock_bot.send_message.call_args[0][1].lower()


def test_cmd_imagine_falls_back_to_link_on_send_failure():
    """If Telegram can't fetch/generate the image, the user still gets the
    raw link instead of a silent failure."""
    with (
        patch("bot.handlers.build_image_url", return_value="http://img/x"),
        patch("bot.handlers.bot") as mock_bot,
    ):
        mock_bot.send_photo.side_effect = Exception("failed to get HTTP URL content")
        from bot.handlers import cmd_imagine

        cmd_imagine(make_message(text="/imagine a rose"))
        sent = mock_bot.send_message.call_args[0][1]
        assert "http://img/x" in sent


# ── /about ────────────────────────────────────────────────────────────────────


def test_cmd_about_with_sqlite():
    """When SQLite is configured, /about should reference SQLite."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.HF_SPACE_ID", ""),
    ):
        from bot.handlers import cmd_about

        cmd_about(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "SQLite" in sent
        assert "stateless" not in sent


def test_cmd_about_includes_commit_sha_when_set():
    """When COMMIT_SHA is populated (worker booted inside a git repo),
    /about exposes a Version line so users can validate which commit is
    live."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.HF_SPACE_ID", ""),
        patch("bot.handlers.COMMIT_SHA", "abc1234"),
    ):
        from bot.handlers import cmd_about

        cmd_about(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "Version: abc1234" in sent


def test_cmd_about_omits_version_line_when_sha_unknown():
    """If git rev-parse failed at boot, the Version line is dropped
    entirely rather than showing 'unknown' — clearer for the user."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.HF_SPACE_ID", ""),
        patch("bot.handlers.COMMIT_SHA", ""),
    ):
        from bot.handlers import cmd_about

        cmd_about(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "Version" not in sent


def test_cmd_about_without_store():
    """When no backend is configured, /about must say stateless. Regression
    guard for the NameError that occurred when `store` was missing from
    bot.handlers' imports."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.store", None),
        patch("bot.handlers.HF_SPACE_ID", ""),
    ):
        from bot.handlers import cmd_about

        cmd_about(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "stateless" in sent


# ── /sha ─────────────────────────────────────────────────────────────────────


def test_cmd_sha_reports_live_commit_sha():
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.COMMIT_SHA", "abc1234"),
    ):
        from bot.handlers import cmd_sha

        cmd_sha(make_message())
        mock_bot.send_message.assert_called_once_with(456, "Live SHA: abc1234")


def test_cmd_sha_reports_unknown_when_git_sha_unavailable():
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.COMMIT_SHA", ""),
    ):
        from bot.handlers import cmd_sha

        cmd_sha(make_message())
        mock_bot.send_message.assert_called_once_with(456, "Live SHA: unknown")


# ── /model command ────────────────────────────────────────────────────────────


def _import_cmd_model_with_hf_enabled():
    """Re-import handlers module with HF_SPACE_ID set so cmd_model exists."""
    import importlib
    import bot.config
    import bot.handlers

    original = bot.config.HF_SPACE_ID
    bot.config.HF_SPACE_ID = "fake/space"
    # Also patch the import in handlers module (already imported via `from ... import HF_SPACE_ID`)
    bot.handlers.HF_SPACE_ID = "fake/space"
    importlib.reload(bot.handlers)
    cmd_model = getattr(bot.handlers, "cmd_model", None)
    # Restore
    bot.config.HF_SPACE_ID = original
    bot.handlers.HF_SPACE_ID = original
    return cmd_model


def test_cmd_model_no_args_shows_current():
    cmd_model = _import_cmd_model_with_hf_enabled()
    assert cmd_model is not None
    with (
        patch("bot.handlers.get_provider", return_value="main"),
        patch("bot.handlers.bot") as mock_bot,
    ):
        msg = make_message(text="/model")
        cmd_model(msg)
        sent = mock_bot.send_message.call_args[0][1]
        assert "Current provider: main" in sent
        assert "/model main" in sent
        assert "/model hf" in sent


def test_cmd_model_switch_to_hf():
    cmd_model = _import_cmd_model_with_hf_enabled()
    with (
        patch("bot.handlers.set_provider", return_value=True) as mock_set,
        patch("bot.handlers.bot") as mock_bot,
    ):
        msg = make_message(text="/model hf")
        cmd_model(msg)
        mock_set.assert_called_once_with(123, "hf")
        sent = mock_bot.send_message.call_args[0][1]
        assert "hf" in sent
        assert "Armenian" in sent


def test_cmd_model_switch_to_main():
    cmd_model = _import_cmd_model_with_hf_enabled()
    with (
        patch("bot.handlers.set_provider", return_value=True) as mock_set,
        patch("bot.handlers.bot") as mock_bot,
    ):
        msg = make_message(text="/model main")
        cmd_model(msg)
        mock_set.assert_called_once_with(123, "main")
        sent = mock_bot.send_message.call_args[0][1]
        assert "Main" in sent


def test_cmd_model_invalid_choice():
    cmd_model = _import_cmd_model_with_hf_enabled()
    with (
        patch("bot.handlers.set_provider") as mock_set,
        patch("bot.handlers.bot") as mock_bot,
    ):
        msg = make_message(text="/model bogus")
        cmd_model(msg)
        mock_set.assert_not_called()
        assert "Invalid" in mock_bot.send_message.call_args[0][1]


def test_cmd_model_redis_error_reports_failure():
    cmd_model = _import_cmd_model_with_hf_enabled()
    with (
        patch("bot.handlers.set_provider", return_value=False),
        patch("bot.handlers.bot") as mock_bot,
    ):
        msg = make_message(text="/model hf")
        cmd_model(msg)
        assert "Could not save" in mock_bot.send_message.call_args[0][1]


def test_cmd_model_not_registered_without_hf_space_id():
    """When HF_SPACE_ID is empty, cmd_model should not exist."""
    import importlib
    import bot.config
    import bot.handlers

    bot.config.HF_SPACE_ID = ""
    bot.handlers.HF_SPACE_ID = ""
    # reload() doesn't delete existing attributes, so clear it first
    if hasattr(bot.handlers, "cmd_model"):
        delattr(bot.handlers, "cmd_model")
    importlib.reload(bot.handlers)
    assert not hasattr(bot.handlers, "cmd_model")


def test_handle_message_uses_keep_typing():
    """handle_message should wrap ask_ai in the keep_typing context."""
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai", return_value="reply"),
        patch("bot.handlers.send_reply"),
        patch("bot.handlers.keep_typing") as mock_keep,
        patch("bot.handlers.bot"),
    ):
        mock_keep.return_value.__enter__ = MagicMock(return_value=None)
        mock_keep.return_value.__exit__ = MagicMock(return_value=None)
        from bot.handlers import handle_message

        msg = make_message()
        handle_message(msg)
        mock_keep.assert_called_once_with(456)
