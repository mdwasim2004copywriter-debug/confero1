"""
Simple email/password authentication.

Passwords are hashed with bcrypt, a slow, purpose-built password-hashing
algorithm (deliberately slow to resist brute-forcing), not a fast general
hash like MD5/SHA. The password itself is never stored anywhere.

Sessions are stateless JWT tokens: the server signs a token containing the
user's id, the frontend sends it back in an Authorization header on every
request, and the server just verifies the signature, no server-side
session storage needed, which fits a small local app well.
"""

import os
import time

import bcrypt
import jwt
from fastapi import Header, HTTPException

JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-secret-change-this-in-real-use")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 60 * 60 * 24 * 30  # 30 days


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: str, email: str) -> str:
    payload = {"sub": user_id, "email": email, "exp": int(time.time()) + JWT_EXPIRY_SECONDS}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def get_current_user(authorization: str = Header(None)) -> dict:
    """FastAPI dependency, add `user = Depends(get_current_user)` to any
    endpoint that should require login. Raises 401 if there's no valid
    token, so the endpoint body never runs for an unauthenticated request."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not logged in")
    token = authorization[len("Bearer "):]
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid login session")
    return {"id": payload["sub"], "email": payload["email"]}


def get_current_user_optional(authorization: str = Header(None)) -> dict | None:
    """Like get_current_user, but for endpoints guests may also use: no
    Authorization header at all means "guest", so this returns None instead
    of raising. A header that IS present but invalid/expired still raises,
    a broken session shouldn't silently be treated as an intentional guest."""
    if not authorization:
        return None
    return get_current_user(authorization)


# ---------------------------------------------------------------------
# Local CAPTCHA, a simple math challenge, not a real bot-detection
# service (that would require an external provider like reCAPTCHA, which
# needs its own account/API key and an internet round-trip). This stops
# trivial automated signup spam without adding any external dependency;
# it is not meant to stand up to a determined attacker. Reuses the same
# signed-token approach as login, so the server doesn't need to remember
# which question it asked anyone, the answer travels inside the token.
CAPTCHA_EXPIRY_SECONDS = 5 * 60


def create_captcha_token(answer: int) -> str:
    payload = {"captcha_answer": answer, "exp": int(time.time()) + CAPTCHA_EXPIRY_SECONDS}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_captcha(user_answer: int, token: str) -> bool:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("captcha_answer") == user_answer
    except Exception:
        return False