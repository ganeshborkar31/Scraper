"""Canonical matching keys while retaining source values on each observation."""

import re
import unicodedata

from app.schemas import normalize_domain, normalize_name


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    extension_match = re.search(r"(?:ext\.?|x)\s*(\d+)\s*$", text, re.IGNORECASE)
    extension = extension_match.group(1) if extension_match else None
    main = text[: extension_match.start()] if extension_match else text
    digits = "".join(char for char in main if char.isdigit())
    if not digits:
        return None
    international = main.lstrip().startswith("+") or digits.startswith("00")
    if digits.startswith("00"):
        digits = digits[2:]
        international = True
    prefix = "+" if international else ""
    suffix = f"x{extension}" if extension else ""
    return f"{prefix}{digits}{suffix}"


def normalize_address(value: str | None) -> str | None:
    if not value:
        return None
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    normalized = " ".join("".join(char if char.isalnum() else " " for char in plain).split())
    return normalized or None


def normalize_coordinates(
    latitude: float | None, longitude: float | None
) -> tuple[float | None, float | None]:
    return (
        round(latitude, 6) if latitude is not None else None,
        round(longitude, 6) if longitude is not None else None,
    )


__all__ = [
    "normalize_address",
    "normalize_coordinates",
    "normalize_domain",
    "normalize_name",
    "normalize_phone",
]
