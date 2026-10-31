"""Single-owner password authentication, with persisted throttling and expiring UI sessions."""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from sqlalchemy import select

from connectiq.models import User, utcnow
from connectiq.validation import ValidationError, aware


def hash_password(password):
    if len(password) < 12 or len(password) > 256:
        raise ValidationError("Use a password of 12 to 256 characters.")
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return f"scrypt$16384$8$1${salt}${key.hex()}"


def valid_hash(encoded):
    return bool(re.fullmatch(r"scrypt\$16384\$8\$1\$[0-9a-f]{32}\$[0-9a-f]{128}", encoded))


def verify_password(password, encoded):
    if not valid_hash(encoded) or len(password) > 256:
        return False
    _, n, r, p, salt, expected = encoded.split("$")
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    return hmac.compare_digest(actual.hex(), expected)


def bootstrap_owner(session, config):
    if not config.admin_email or not valid_hash(config.password_hash):
        return None
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", config.admin_email):
        raise ValidationError("ADMIN_EMAIL must be a valid email address.")
    # A configured deployment has one owner. Rotation updates credentials without replacing data.
    user = session.scalar(select(User).order_by(User.id).limit(1).with_for_update())
    if user is None:
        user = User(email=config.admin_email, password_hash=config.password_hash)
        session.add(user)
        session.flush()
    elif user.email != config.admin_email or user.password_hash != config.password_hash:
        user.email = config.admin_email
        user.password_hash = config.password_hash
        user.failed_logins = 0
        user.locked_until = None
    return user.id


def authenticate(session, email, password, now=None):
    now = aware(now or utcnow())
    user = session.scalar(select(User).order_by(User.id).limit(1).with_for_update())
    if not user or (user.locked_until and aware(user.locked_until) > now):
        return None
    password_ok = verify_password(password, user.password_hash)
    if not hmac.compare_digest(email.strip().lower().encode(), user.email.encode()) or not password_ok:
        user.failed_logins += 1
        if user.failed_logins >= 5:
            user.locked_until = now + timedelta(minutes=15)
        return None
    user.failed_logins = 0
    user.locked_until = None
    return user.id


def credential_fingerprint(encoded):
    return hashlib.sha256(encoded.encode()).hexdigest()
