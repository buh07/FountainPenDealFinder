import re
from datetime import datetime, timezone


FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "，": ",",
        "．": ".",
        "￥": "¥",
        "\u3000": " ",
    }
)


PRICE_PATTERNS = [
    re.compile(r"(?:¥|￥)\s*([0-9][0-9,\s]*)", re.IGNORECASE),
    re.compile(r"([0-9][0-9,\s]*)\s*円", re.IGNORECASE),
    re.compile(r"([0-9][0-9,\s]*)\s*JPY", re.IGNORECASE),
]

PRICE_SIGNAL_PATTERNS = [
    re.compile(r"(?:¥|￥)\s*[0-9０-９,\s]+", re.IGNORECASE),
    re.compile(r"[0-9０-９,\s]+\s*円", re.IGNORECASE),
    re.compile(r"[0-9０-９,\s]+\s*JPY", re.IGNORECASE),
    re.compile(r"(?:価格|price)\s*[:：]?\s*[0-9０-９][0-9０-９,\s]*", re.IGNORECASE),
    re.compile(r"(?:価格|price)\s*[:：]", re.IGNORECASE),
    re.compile(r"(?:¥|￥)", re.IGNORECASE),
]


def default_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }


def to_utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def extract_price_jpy(text: str) -> int | None:
    normalized_text = text.translate(FULLWIDTH_TRANSLATION)
    for pattern in PRICE_PATTERNS:
        match = pattern.search(normalized_text)
        if not match:
            continue
        try:
            digits = re.sub(r"[^0-9]", "", match.group(1))
            if not digits:
                continue
            return int(digits)
        except ValueError:
            continue
    return None


def has_price_signal(text: str) -> bool:
    normalized_text = text.translate(FULLWIDTH_TRANSLATION)
    return any(pattern.search(normalized_text) for pattern in PRICE_SIGNAL_PATTERNS)


def parse_price_with_status(text: str) -> tuple[int | None, bool]:
    price = extract_price_jpy(text)
    parse_error = price is None and has_price_signal(text)
    return price, parse_error


def extract_id_from_url(url: str, pattern: str) -> str | None:
    match = re.search(pattern, url)
    if not match:
        return None
    return match.group(1)


def dedupe_preserve_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out
