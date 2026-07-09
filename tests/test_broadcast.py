"""Tests for the /api/broadcast endpoint — the daily-motivation trigger.

Mirrors test_deploy.py's security focus: the secret must be verified and
the endpoint must fail closed when BROADCAST_SECRET is unset.
"""

from unittest.mock import patch, MagicMock


def test_broadcast_fails_closed_when_secret_unset():
    """No BROADCAST_SECRET → the endpoint refuses everything, so a
    misconfigured deploy can't let anyone spam every subscriber."""
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "anything"
    with (
        patch("bot.config.BROADCAST_SECRET", ""),
        patch("api.index.request", mock_request),
    ):
        from api.index import broadcast

        body, status = broadcast()
        assert status == 403


def test_broadcast_rejects_bad_secret():
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "wrong"
    with (
        patch("bot.config.BROADCAST_SECRET", "correct"),
        patch("api.index.request", mock_request),
    ):
        from api.index import broadcast

        body, status = broadcast()
        assert status == 403


def test_broadcast_runs_and_reports_counts():
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "correct"
    with (
        patch("bot.config.BROADCAST_SECRET", "correct"),
        patch("api.index.request", mock_request),
        patch(
            "bot.motivation.broadcast_motivation",
            return_value={"total": 3, "sent": 2, "failed": 1, "removed": 1},
        ) as mock_bcast,
    ):
        from api.index import broadcast

        body, status = broadcast()
        assert status == 200
        mock_bcast.assert_called_once()
        assert "sent=2" in body
        assert "failed=1" in body
        assert "removed=1" in body
        assert "total=3" in body


def test_broadcast_returns_500_on_generation_failure():
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "correct"
    with (
        patch("bot.config.BROADCAST_SECRET", "correct"),
        patch("api.index.request", mock_request),
        patch(
            "bot.motivation.broadcast_motivation",
            side_effect=Exception("AI down"),
        ),
    ):
        from api.index import broadcast

        body, status = broadcast()
        assert status == 500
        assert "AI down" not in body  # don't leak internals to the caller
