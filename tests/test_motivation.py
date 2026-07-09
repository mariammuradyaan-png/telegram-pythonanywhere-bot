from unittest.mock import MagicMock, patch

import bot.motivation as m


class FakeStore:
    """Minimal in-memory stand-in for SqliteStore (get/set only)."""

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value


def test_subscribe_and_is_subscribed():
    with patch.object(m, "store", FakeStore()):
        assert m.subscribe(1, 100) is True
        assert m.is_subscribed(1) is True
        assert m.is_subscribed(2) is False
        assert m.get_subscribers() == {"1": 100}


def test_unsubscribe_removes_only_that_user():
    with patch.object(m, "store", FakeStore()):
        m.subscribe(1, 100)
        m.subscribe(2, 200)
        assert m.unsubscribe(1) is True
        assert m.get_subscribers() == {"2": 200}


def test_unsubscribe_absent_user_is_idempotent():
    with patch.object(m, "store", FakeStore()):
        assert m.unsubscribe(999) is True
        assert m.get_subscribers() == {}


def test_stateless_mode_cannot_subscribe():
    with patch.object(m, "store", None):
        assert m.subscribe(1, 100) is False
        assert m.unsubscribe(1) is False
        assert m.is_subscribed(1) is False
        assert m.get_subscribers() == {}


def test_load_subs_tolerates_corrupt_json():
    fs = FakeStore()
    fs.data[m._SUBS_KEY] = "{not json"
    with patch.object(m, "store", fs):
        assert m.get_subscribers() == {}


def test_build_motivation_message_applies_persona():
    with patch.object(m, "generate", return_value="rise and shine ☀️") as mock_gen:
        out = m.build_motivation_message()
        assert out == "rise and shine ☀️"
        messages = mock_gen.call_args[0][1]
        assert messages[0]["role"] == "system"  # SYSTEM_PROMPT prepended
        assert messages[-1]["role"] == "user"


def test_broadcast_sends_one_message_to_all():
    fs = FakeStore()
    mock_bot = MagicMock()
    with (
        patch.object(m, "store", fs),
        patch.object(m, "bot", mock_bot),
        patch.object(m, "build_motivation_message", return_value="good morning ⭐"),
    ):
        m.subscribe(1, 100)
        m.subscribe(2, 200)
        result = m.broadcast_motivation()
        assert result == {"total": 2, "sent": 2, "failed": 0, "removed": 0}
        mock_bot.send_message.assert_any_call(100, "good morning ⭐")
        mock_bot.send_message.assert_any_call(200, "good morning ⭐")


def test_broadcast_makes_no_ai_call_when_empty():
    with (
        patch.object(m, "store", FakeStore()),
        patch.object(m, "build_motivation_message") as mock_build,
    ):
        result = m.broadcast_motivation()
        assert result == {"total": 0, "sent": 0, "failed": 0, "removed": 0}
        mock_build.assert_not_called()


def test_broadcast_auto_unsubscribes_blocked_users():
    fs = FakeStore()
    mock_bot = MagicMock()
    blocked_error = Exception("bot was blocked by the user")
    blocked_error.error_code = 403

    def send(chat_id, _msg):
        if chat_id == 100:
            raise blocked_error

    mock_bot.send_message.side_effect = send
    with (
        patch.object(m, "store", fs),
        patch.object(m, "bot", mock_bot),
        patch.object(m, "build_motivation_message", return_value="hi"),
    ):
        m.subscribe(1, 100)
        m.subscribe(2, 200)
        result = m.broadcast_motivation()
        assert result["sent"] == 1
        assert result["failed"] == 1
        assert result["removed"] == 1
        # The blocked user is gone; the reachable one stays.
        assert m.get_subscribers() == {"2": 200}


def test_broadcast_keeps_non_403_failures_subscribed():
    """A transient send error (not a 403) should count as failed but NOT
    remove the user — they might be reachable next time."""
    fs = FakeStore()
    mock_bot = MagicMock()
    mock_bot.send_message.side_effect = Exception("timeout")
    with (
        patch.object(m, "store", fs),
        patch.object(m, "bot", mock_bot),
        patch.object(m, "build_motivation_message", return_value="hi"),
    ):
        m.subscribe(1, 100)
        result = m.broadcast_motivation()
        assert result["failed"] == 1
        assert result["removed"] == 0
        assert m.get_subscribers() == {"1": 100}
