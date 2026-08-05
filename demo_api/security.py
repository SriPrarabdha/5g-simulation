from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Identity:
    subject: str
    role: str
    expires_at: int


class TokenService:
    def __init__(self, secret: str | None = None) -> None:
        self.secret = (secret or os.environ.get("CDOT_DEMO_SECRET") or "local-demo-only-change-me").encode()

    def issue(self, subject: str, role: str, ttl_seconds: int = 3600) -> str:
        body = {"sub": subject, "role": role, "exp": int(time.time()) + ttl_seconds}
        encoded = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).rstrip(b"=")
        signature = hmac.new(self.secret, encoded, hashlib.sha256).digest()
        return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"

    def verify(self, token: str) -> Identity:
        try:
            body, signature = token.split(".", 1)
            expected = hmac.new(self.secret, body.encode(), hashlib.sha256).digest()
            actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
            if not hmac.compare_digest(expected, actual):
                raise ValueError("invalid signature")
            payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            if int(payload["exp"]) <= int(time.time()):
                raise ValueError("expired token")
            if payload["role"] not in {"presenter", "viewer"}:
                raise ValueError("invalid role")
            return Identity(payload["sub"], payload["role"], int(payload["exp"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("invalid or expired token") from error

