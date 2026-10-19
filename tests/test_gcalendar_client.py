"""CalendarClient status handling, with the HTTP layer faked (no network, no key file)."""

import pytest

from honda_voice.integrations import gcalendar
from honda_voice.integrations.gcalendar import CalendarClient, CalendarError


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        gcalendar.service_account.Credentials, "from_service_account_file", lambda *a, **k: object()
    )
    c = CalendarClient("unused.json", "abc@group.calendar.google.com")
    c.responses = []

    async def fake_request(method, url, json=None):
        assert "abc%40group.calendar.google.com" in url  # calendar id is URL-encoded
        return c.responses.pop(0)

    c._request = fake_request
    return c


async def test_create_and_idempotent_retry(client):
    client.responses = [(200, {"id": "evt1"}), (409, {"error": {"message": "duplicate"}})]
    assert await client.create_event({"id": "evt1"}) == "evt1"
    assert await client.create_event({"id": "evt1"}) == "evt1"  # already there: done, no duplicate


@pytest.mark.parametrize(("status", "retryable"), [(403, False), (404, False), (429, True), (503, True)])
async def test_errors(client, status, retryable):
    client.responses = [(status, {"error": {"message": "nope"}})]
    with pytest.raises(CalendarError) as e:
        await client.create_event({"id": "evt1"})
    assert e.value.retryable is retryable and "nope" in str(e.value)


async def test_delete_and_get(client):
    client.responses = [(204, None), (410, None), (200, {"status": "cancelled"}), (404, None)]
    await client.delete_event("evt1")
    await client.delete_event("evt1")  # already gone counts as deleted
    assert await client.get_event("evt1") is None
    assert await client.get_event("evt1") is None


def test_network_errors_are_retryable():
    assert CalendarError(0, "network").retryable
