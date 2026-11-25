"""Address encodings: keccak-256 / EIP-55, base58(check), Tron hex<->base58, bech32.

Pure Python, no third-party crypto. Keccak is only used to check EIP-55 checksums
when tracked wallets load, so speed does not matter.
"""

from __future__ import annotations

import hashlib
import re

# ---------------------------------------------------------------- keccak-256

_KECCAK_RC = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)
_KECCAK_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_MASK64 = (1 << 64) - 1


def _rol(value: int, shift: int) -> int:
    shift %= 64
    return ((value << shift) | (value >> (64 - shift))) & _MASK64


def _keccak_f(state: list[list[int]]) -> None:
    for rc in _KECCAK_RC:
        c = [
            state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4]
            for x in range(5)
        ]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(state[x][y], _KECCAK_ROT[x][y])
        for x in range(5):
            for y in range(5):
                state[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        state[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Ethereum's Keccak-256 (original padding 0x01, not SHA3-256)."""
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate:
        padded.append(0)
    padded[-1] |= 0x80
    state = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(padded), rate):
        block = padded[offset : offset + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[8 * i : 8 * i + 8], "little")
            state[i % 5][i // 5] ^= lane
        _keccak_f(state)
    out = b"".join(state[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out[:32]


# ------------------------------------------------------------------- EVM

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def eip55_checksum(address: str) -> str:
    """Return the EIP-55 mixed-case form of a 0x address."""
    if not _EVM_RE.match(address or ""):
        raise ValueError(f"not an EVM address: {address!r}")
    lower = address[2:].lower()
    digest = keccak256(lower.encode("ascii")).hex()
    return "0x" + "".join(
        ch.upper() if ch.isalpha() and int(digest[i], 16) >= 8 else ch
        for i, ch in enumerate(lower)
    )


def is_valid_eip55(address: str) -> bool:
    """True only for a correctly checksummed mixed-case address."""
    if not _EVM_RE.match(address or ""):
        return False
    return eip55_checksum(address) == address


# ---------------------------------------------------------------- base58

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {ch: i for i, ch in enumerate(_B58_ALPHABET)}


def b58encode(data: bytes) -> str:
    num = int.from_bytes(data, "big")
    out = ""
    while num:
        num, rem = divmod(num, 58)
        out = _B58_ALPHABET[rem] + out
    pad = len(data) - len(data.lstrip(b"\0"))
    return "1" * pad + out


def b58decode(text: str) -> bytes:
    num = 0
    for ch in text:
        if ch not in _B58_INDEX:
            raise ValueError(f"invalid base58 character {ch!r}")
        num = num * 58 + _B58_INDEX[ch]
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\0" * pad + body


def _checksum4(payload: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]


def b58check_encode(payload: bytes) -> str:
    return b58encode(payload + _checksum4(payload))


def b58check_decode(text: str) -> bytes:
    raw = b58decode(text)
    if len(raw) < 5:
        raise ValueError("base58check too short")
    payload, check = raw[:-4], raw[-4:]
    if _checksum4(payload) != check:
        raise ValueError("bad base58check checksum")
    return payload


# ------------------------------------------------------------------- Tron

_TRON_PREFIX = 0x41


def tron_hex_to_base58(hex_address: str) -> str:
    """'41ab..'/'0xab..'/'ab..' (20 bytes) -> 'T...'."""
    raw = (hex_address or "").strip().lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    if len(raw) == 40:
        raw = "41" + raw
    if len(raw) != 42 or not raw.startswith("41"):
        raise ValueError(f"not a Tron hex address: {hex_address!r}")
    return b58check_encode(bytes.fromhex(raw))


def tron_base58_to_hex(address: str) -> str:
    """'T...' -> '41' + 40 hex, lowercase."""
    payload = b58check_decode((address or "").strip())
    if len(payload) != 21 or payload[0] != _TRON_PREFIX:
        raise ValueError(f"not a Tron address: {address!r}")
    return payload.hex()


def is_valid_tron(address: str) -> bool:
    try:
        tron_base58_to_hex(address)
    except ValueError:
        return False
    return address.startswith("T")


# ----------------------------------------------------------------- Solana


def is_valid_solana(address: str) -> bool:
    """Solana keys have no checksum; require base58 that decodes to 32 bytes."""
    raw = (address or "").strip()
    if not 32 <= len(raw) <= 44:
        return False
    try:
        return len(b58decode(raw)) == 32
    except ValueError:
        return False


# ---------------------------------------------------------------- Bitcoin

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


def _bech32_polymod(values: list[int]) -> int:
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data: list[int], frombits: int, tobits: int) -> list[int] | None:
    acc = 0
    bits = 0
    out: list[int] = []
    maxv = (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append((acc >> bits) & maxv)
    if bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return out


def _is_valid_segwit(address: str) -> bool:
    if address.lower() != address and address.upper() != address:
        return False
    addr = address.lower()
    pos = addr.rfind("1")
    if pos < 1 or pos + 7 > len(addr) or len(addr) > 90:
        return False
    hrp, data_part = addr[:pos], addr[pos + 1 :]
    if hrp != "bc" or any(c not in _BECH32_CHARSET for c in data_part):
        return False
    data = [_BECH32_CHARSET.find(c) for c in data_part]
    const = _bech32_polymod(_hrp_expand(hrp) + data)
    if const not in (_BECH32_CONST, _BECH32M_CONST):
        return False
    version = data[0]
    program = _convertbits(data[1:-6], 5, 8)
    if program is None or not 2 <= len(program) <= 40 or version > 16:
        return False
    if version == 0:
        return const == _BECH32_CONST and len(program) in (20, 32)
    return const == _BECH32M_CONST


def is_valid_bitcoin(address: str) -> bool:
    """Mainnet P2PKH/P2SH (base58check) or segwit v0/v1+ (bech32/bech32m)."""
    raw = (address or "").strip()
    if raw.lower().startswith("bc1"):
        return _is_valid_segwit(raw)
    try:
        payload = b58check_decode(raw)
    except ValueError:
        return False
    return len(payload) == 21 and payload[0] in (0x00, 0x05)
