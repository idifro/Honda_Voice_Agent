"""Give the read-only `frontend_reader` role (migration 004) a fresh random password and write its connection
URL to frontend/.env.local (git-ignored), replacing DATABASE_URL there. Re-run to rotate the password.

Only a SCRAM-SHA-256 verifier is sent to Postgres, so the plaintext password never reaches the server or its
logs; it is written only to frontend/.env.local (mode 600). Nothing secret is printed.

Run: `uv run python scripts/set_frontend_db_password.py`
"""

import asyncio
import base64
import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from honda_voice.config import get_settings
from honda_voice.db.pool import connect

ROLE = "frontend_reader"
ENV_FILE = Path(__file__).resolve().parents[1] / "frontend" / ".env.local"
ITERATIONS = 4096


def scram_verifier(password: str) -> str:
    """The SCRAM-SHA-256 secret Postgres stores (RFC 5802/7677), computed client-side."""
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    stored_key = hashlib.sha256(hmac.new(salted, b"Client Key", "sha256").digest()).digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"SCRAM-SHA-256${ITERATIONS}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def frontend_url(admin_url: str, password: str) -> str:
    """Same host/port/database; the Supabase pooler wants '<role>.<project_ref>' as the user name."""
    parts = urlsplit(admin_url)
    admin_user = parts.username or ""
    user = f"{ROLE}.{admin_user.split('.', 1)[1]}" if "." in admin_user else ROLE
    netloc = f"{quote(user, safe='.')}:{quote(password, safe='')}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


async def main() -> None:
    admin_url = get_settings().database_url.get_secret_value()
    password = secrets.token_urlsafe(32)
    verifier = scram_verifier(password)
    assert re.fullmatch(r"[A-Za-z0-9+/=:$\-]+", verifier)  # safe to inline: no quotes possible
    conn = await connect()
    try:
        await conn.execute(f"alter role {ROLE} with login password '{verifier}'")
    finally:
        await conn.close()

    line = f"DATABASE_URL={frontend_url(admin_url, password)}"
    text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    if re.search(r"(?m)^DATABASE_URL=.*$", text):
        text = re.sub(r"(?m)^DATABASE_URL=.*$", lambda _: line, text)
    else:
        text += (
            f"\n# Read-only role (migration 004), written by scripts/set_frontend_db_password.py\n{line}\n"
        )
    ENV_FILE.write_text(text)
    os.chmod(ENV_FILE, 0o600)
    print(f"{ROLE}: new password set; DATABASE_URL updated in {ENV_FILE.relative_to(Path.cwd())}")


if __name__ == "__main__":
    asyncio.run(main())
