import os
import re
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_LENGTH = 26
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def new_request_id() -> str:
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    characters = []
    for _ in range(_ULID_LENGTH):
        characters.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(characters))


def sanitize_request_id(raw: str | None) -> str:
    if raw and _SAFE_ID.match(raw):
        return raw
    return new_request_id()
