"""Google Calendar REST client for the bookings mirror (SPEC §11). Async (aiohttp); the service-account token
is signed by google-auth and refreshed in a thread (about once an hour). No attendees: that would need
Workspace domain-wide delegation.

Verified 2026-10-15 against Calendar API v3 (events.insert / get / delete) with google-auth 2.43.0.
"""

import asyncio
from typing import Any
from urllib.parse import quote

import aiohttp
from google.auth.transport.requests import Request
from google.oauth2 import service_account

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
BASE_URL = "https://www.googleapis.com/calendar/v3"


class CalendarError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"calendar API {status}: {message}")
        self.status = status

    @property
    def retryable(self) -> bool:
        """Rate limits, server errors and network failures (status 0) are worth retrying; other 4xx aren't."""
        return self.status in (0, 408, 429) or self.status >= 500


class CalendarClient:
    def __init__(self, key_path: str, calendar_id: str, timeout_s: float = 10.0) -> None:
        self._creds = service_account.Credentials.from_service_account_file(key_path, scopes=SCOPES)
        self._events_url = f"{BASE_URL}/calendars/{quote(calendar_id, safe='')}/events"
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: aiohttp.ClientSession | None = None
        self._token_lock = asyncio.Lock()

    async def _headers(self) -> dict[str, str]:
        async with self._token_lock:
            if not self._creds.valid:
                await asyncio.to_thread(self._creds.refresh, Request())
        return {"Authorization": f"Bearer {self._creds.token}"}

    async def _request(self, method: str, url: str, json: dict | None = None) -> tuple[int, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        try:
            async with self._session.request(method, url, json=json, headers=await self._headers()) as resp:
                body = await resp.json(content_type=None) if resp.status != 204 else None
                return resp.status, body
        except (aiohttp.ClientError, TimeoutError) as e:
            raise CalendarError(0, f"network: {e!r}") from e

    @staticmethod
    def _message(body: Any) -> str:
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            return str(body["error"].get("message", ""))[:300]
        return ""

    async def create_event(self, event: dict[str, Any]) -> str:
        """Insert the event; returns its id. With a fixed `id`, a repeat insert (409) counts as done, so a
        retry after an unclear failure never creates a duplicate."""
        status, body = await self._request("POST", self._events_url, json=event)
        if status == 200:
            return body["id"]
        if status == 409 and event.get("id"):
            return event["id"]
        raise CalendarError(status, self._message(body))

    async def get_event(self, event_id: str) -> dict[str, Any] | None:
        status, body = await self._request("GET", f"{self._events_url}/{event_id}")
        if status == 200:
            return None if body.get("status") == "cancelled" else body
        if status in (404, 410):
            return None
        raise CalendarError(status, self._message(body))

    async def delete_event(self, event_id: str) -> None:
        """Delete; an event that's already gone (404/410) counts as deleted."""
        status, body = await self._request("DELETE", f"{self._events_url}/{event_id}")
        if status not in (200, 204, 404, 410):
            raise CalendarError(status, self._message(body))

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
