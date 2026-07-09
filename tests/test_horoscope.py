from unittest.mock import MagicMock, patch

import bot.horoscope as h


class FakeStore:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value


# ── sign_from_date ──────────────────────────────────────────────────────────────


def test_sign_from_date_covers_all_twelve():
    # One clearly-inside-the-range date per sign.
    cases = {
        (4, 10): "aries",
        (5, 5): "taurus",
        (6, 10): "gemini",
        (7, 4): "cancer",
        (8, 1): "leo",
        (9, 10): "virgo",
        (10, 5): "libra",
        (11, 5): "scorpio",
        (12, 5): "sagittarius",
        (1, 5): "capricorn",
        (2, 5): "aquarius",
        (3, 5): "pisces",
    }
    for (month, day), sign in cases.items():
        assert h.sign_from_date(month, day) == sign, (month, day)


def test_sign_from_date_boundaries():
    # Cusp edges must land on the right side.
    assert h.sign_from_date(9, 22) == "virgo"  # last Virgo day
    assert h.sign_from_date(9, 23) == "libra"  # first Libra day
    assert h.sign_from_date(12, 21) == "sagittarius"
    assert h.sign_from_date(12, 22) == "capricorn"  # wraps back
    assert h.sign_from_date(1, 19) == "capricorn"
    assert h.sign_from_date(1, 20) == "aquarius"


# ── parse_sign ──────────────────────────────────────────────────────────────────


def test_parse_sign_by_name():
    assert h.parse_sign("Leo") == "leo"
    assert h.parse_sign("  scorpio  ") == "scorpio"


def test_parse_sign_by_birthday_formats():
    assert h.parse_sign("1998-07-23") == "leo"  # YYYY-MM-DD
    assert h.parse_sign("07-23") == "leo"  # MM-DD
    assert h.parse_sign("07/23") == "leo"  # slashes
    assert h.parse_sign("07.23") == "leo"  # dots


def test_parse_sign_rejects_garbage():
    assert h.parse_sign("banana") is None
    assert h.parse_sign("13-40") is None  # impossible month/day
    assert h.parse_sign("") is None


# ── opt-in storage ──────────────────────────────────────────────────────────────


def test_subscribe_stores_sign_and_chat():
    with patch.object(h, "store", FakeStore()):
        assert h.subscribe(1, 100, "leo") is True
        assert h.get_subscriber(1) == {"chat_id": 100, "sign": "leo"}
        assert h.get_subscriber(2) is None


def test_unsubscribe_and_stateless():
    with patch.object(h, "store", FakeStore()):
        h.subscribe(1, 100, "leo")
        assert h.unsubscribe(1) is True
        assert h.get_subscriber(1) is None
    with patch.object(h, "store", None):
        assert h.subscribe(1, 100, "leo") is False
        assert h.get_subscriber(1) is None


# ── broadcast ───────────────────────────────────────────────────────────────────


def test_broadcast_generates_once_per_sign_and_sends_each_theirs():
    fs = FakeStore()
    mock_bot = MagicMock()
    with (
        patch.object(h, "store", fs),
        patch.object(h, "bot", mock_bot),
        patch.object(
            h, "build_horoscope_message", side_effect=lambda s: f"reading for {s}"
        ) as mock_build,
    ):
        h.subscribe(1, 100, "leo")
        h.subscribe(2, 200, "leo")  # same sign as user 1
        h.subscribe(3, 300, "aries")
        result = h.broadcast_horoscopes()
        assert result == {"total": 3, "sent": 3, "failed": 0, "removed": 0}
        # Two distinct signs => exactly two generations, not three.
        assert mock_build.call_count == 2
        mock_bot.send_message.assert_any_call(100, "reading for leo")
        mock_bot.send_message.assert_any_call(200, "reading for leo")
        mock_bot.send_message.assert_any_call(300, "reading for aries")


def test_broadcast_empty_makes_no_ai_call():
    with (
        patch.object(h, "store", FakeStore()),
        patch.object(h, "build_horoscope_message") as mock_build,
    ):
        assert h.broadcast_horoscopes() == {
            "total": 0,
            "sent": 0,
            "failed": 0,
            "removed": 0,
        }
        mock_build.assert_not_called()


def test_broadcast_auto_unsubscribes_blocked_users():
    fs = FakeStore()
    mock_bot = MagicMock()
    err = Exception("blocked")
    err.error_code = 403

    def send(chat_id, _msg):
        if chat_id == 100:
            raise err

    mock_bot.send_message.side_effect = send
    with (
        patch.object(h, "store", fs),
        patch.object(h, "bot", mock_bot),
        patch.object(h, "build_horoscope_message", return_value="hi"),
    ):
        h.subscribe(1, 100, "leo")
        h.subscribe(2, 200, "aries")
        result = h.broadcast_horoscopes()
        assert result["sent"] == 1
        assert result["removed"] == 1
        assert h.get_subscriber(1) is None
        assert h.get_subscriber(2) == {"chat_id": 200, "sign": "aries"}


def test_build_horoscope_message_applies_persona_and_sign():
    with patch.object(h, "generate", return_value="the stars love you ✨") as mock_gen:
        out = h.build_horoscope_message("leo")
        assert out == "the stars love you ✨"
        messages = mock_gen.call_args[0][1]
        assert messages[0]["role"] == "system"
        assert "Leo" in messages[-1]["content"]  # sign interpolated


# ── /api/horoscope endpoint ──────────────────────────────────────────────────────


def test_endpoint_fails_closed_when_secret_unset():
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "anything"
    with (
        patch("bot.config.BROADCAST_SECRET", ""),
        patch("api.index.request", mock_request),
    ):
        from api.index import horoscope as endpoint

        _, status = endpoint()
        assert status == 403


def test_endpoint_rejects_bad_secret():
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "wrong"
    with (
        patch("bot.config.BROADCAST_SECRET", "correct"),
        patch("api.index.request", mock_request),
    ):
        from api.index import horoscope as endpoint

        _, status = endpoint()
        assert status == 403


def test_endpoint_runs_and_reports_counts():
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "correct"
    with (
        patch("bot.config.BROADCAST_SECRET", "correct"),
        patch("api.index.request", mock_request),
        patch(
            "bot.horoscope.broadcast_horoscopes",
            return_value={"total": 2, "sent": 2, "failed": 0, "removed": 0},
        ) as mock_bcast,
    ):
        from api.index import horoscope as endpoint

        body, status = endpoint()
        assert status == 200
        mock_bcast.assert_called_once()
        assert "sent=2" in body
