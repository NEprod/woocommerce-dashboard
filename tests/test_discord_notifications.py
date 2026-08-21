import importlib

import pytest
import requests

from app.utils import discord


class Response:
    def __init__(self, status_code=204, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


@pytest.fixture
def discord_enabled(monkeypatch):
    monkeypatch.setenv("DISCORD_ENABLED", "true")
    monkeypatch.setenv("DISCORD_WEBHOOK_SCANS_INFO", "https://discord.com/api/webhooks/test/value")
    monkeypatch.setenv("DISCORD_WEBHOOK_SCANS_ERRORS", "https://discord.com/api/webhooks/test/error")
    module = importlib.reload(discord)
    yield module
    monkeypatch.setenv("DISCORD_ENABLED", "false")
    importlib.reload(discord)


def test_discord_disabled_and_missing_configuration_skip_safely(monkeypatch):
    monkeypatch.setenv("DISCORD_ENABLED", "false")
    module = importlib.reload(discord)
    assert module.notify_scan_started("append", 2)[0] is False
    assert module.configuration_summary()["state"] == "disabled"


def test_existing_embed_style_and_success_are_preserved(discord_enabled, monkeypatch):
    calls = []
    monkeypatch.setattr(discord_enabled.requests, "post", lambda url, **kwargs: calls.append((url, kwargs)) or Response())
    assert discord_enabled.notify_scan_started("append", 3) == (True, "sent")
    payload = calls[0][1]["json"]
    assert payload["embeds"][0]["title"] == "Scan Started"
    assert payload["embeds"][0]["color"] == discord_enabled.COLORS["info"]
    assert "footer" in payload["embeds"][0]


def test_warning_completion_uses_warning_style(discord_enabled, monkeypatch):
    calls = []
    monkeypatch.setattr(discord_enabled.requests, "post", lambda url, **kwargs: calls.append(kwargs["json"]) or Response())
    assert discord_enabled.notify_scan_completed("update", {"warnings": 2}, "00:04")[0]
    embed = calls[0]["embeds"][0]
    assert embed["title"] == "Scan Completed with Warnings"
    assert embed["color"] == discord_enabled.COLORS["warn"]


@pytest.mark.parametrize("failure", [requests.Timeout("timeout"), requests.ConnectionError("connection")])
def test_transport_failures_are_bounded_and_sanitized(discord_enabled, monkeypatch, failure):
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(1)
        raise failure
    monkeypatch.setattr(discord_enabled.requests, "post", fail)
    ok, message = discord_enabled.notify_scan_started("append", 1)
    assert ok is False
    assert len(attempts) == 2
    assert "discord.com" not in message


def test_rate_limit_is_retried_once(discord_enabled, monkeypatch):
    responses = iter([Response(429, {"Retry-After": "0"}), Response(204)])
    calls = []
    monkeypatch.setattr(discord_enabled.requests, "post", lambda *a, **k: calls.append(1) or next(responses))
    assert discord_enabled.notify_scan_started("append", 1) == (True, "sent")
    assert len(calls) == 2


def test_invalid_webhook_never_attempts_transport(discord_enabled, monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_SCANS_INFO", "https://example.invalid/secret")
    monkeypatch.setattr(discord_enabled.requests, "post", lambda *a, **k: pytest.fail("network called"))
    ok, message = discord_enabled.notify_scan_started("append", 1)
    assert not ok and message == "invalid webhook configuration"


def test_failure_payload_redacts_secrets_and_paths(discord_enabled, monkeypatch):
    payloads = []
    monkeypatch.setattr(discord_enabled.requests, "post", lambda url, **kwargs: payloads.append(kwargs["json"]) or Response())
    discord_enabled.notify_scan_failed("full", "Authorization: Bearer top-secret at /Users/person/catalogue")
    serialized = str(payloads)
    assert "top-secret" not in serialized
    assert "/Users/person" not in serialized


def test_ingest_event_remains_supported(discord_enabled, monkeypatch):
    discord_enabled.WEBHOOKS["ingest"] = "https://discord.com/api/webhooks/test/ingest"
    monkeypatch.setattr(discord_enabled.requests, "post", lambda *a, **k: Response())
    assert discord_enabled.notify_ingest_product(
        sku="FIX-1", name="Fictional", product_type="simple", images_count=1,
        has_shared=True, has_override=False, folder_path="Folder/Product",
    )[0]
