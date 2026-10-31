import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit


class ValidationError(ValueError):
    pass


class DuplicateError(ValidationError):
    pass


def text_value(value, label, maximum=200, required=False, preserve=False):
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text.")
    result = value if preserve else value.strip()
    if required and not result.strip():
        raise ValidationError(f"{label} is required.")
    if len(result) > maximum or any(ord(c) < 32 and c not in "\n\t\r" for c in result):
        raise ValidationError(f"{label} is invalid or exceeds {maximum} characters.")
    return result


def name_key(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def profile_url(value):
    value = text_value(value, "LinkedIn profile URL", 500, True)
    if not value.startswith(("https://", "http://")):
        value = "https://" + value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https") or parts.username or parts.password or parts.port:
            raise ValueError
        if host not in ("linkedin.com", "www.linkedin.com", "in.linkedin.com"):
            raise ValueError
        slug = unquote(parts.path).strip("/")
        if not re.fullmatch(r"in/[A-Za-z0-9_\-\u0080-\uffff]+", slug):
            raise ValueError
    except ValueError:
        raise ValidationError("Use a LinkedIn person URL such as https://www.linkedin.com/in/profile-name.") from None
    return "https://www.linkedin.com/" + slug.lower()


def website_url(value):
    value = text_value(value, "Website", 1000)
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("https", "http") or not parts.hostname or "." not in parts.hostname or parts.username or parts.password:
            raise ValueError
        if any(c.isspace() for c in value):
            raise ValueError
    except ValueError:
        raise ValidationError("Website must be a complete HTTP or HTTPS URL.") from None
    return value


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def past_instant(value, now=None):
    if not isinstance(value, datetime):
        raise ValidationError("A valid date and time is required.")
    value = aware(value)
    if value > aware(now or datetime.now(timezone.utc)):
        raise ValidationError("Activity dates cannot be in the future.")
    return value
