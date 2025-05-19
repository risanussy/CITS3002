"""
protocol_enc.py – Encrypted Battleship framing layer (Tier‑4.3)
==============================================================
Implements:
  • AES‑256 in CTR mode for payload confidentiality
  • CRC‑32 for corruption detection
  • Monotonic sequence-number + unique nonce per packet for replay defence
  • Tiny 2‑byte length prefix for TCP stream framing

Frame layout (little‑endian)
---------------------------
Offset  Size  Field            Notes
0       1     type             1 = GAME, 2 = CHAT, 3 = CTRL
1       4     seq              uint32, starts at 1, ++ per packet (anti‑replay)
5       2     len              uint16, length of ciphertext "ct"
7       8     nonce            uint64, unique per packet, used as AES CTR nonce
15      len   ct               AES‑CTR(ciphertext(payload))
15+len  4     crc32            CRC‑32 of **header+ct** (offset 0..14+len‑1)
Total   19+len bytes

Notes
-----
*   Header ( type|seq|len|nonce ) is **plain‑text** so receiver can check seq & crc
    before decrypting.
*   Nonce uniqueness: each connection maintains an 8‑byte counter seeded with
    a cryptographically random value → no reuse.
*   Out‑of‑band shared key (32‑byte) is loaded from `shared.key` file if exists,
    else uses demo fallback (NOT safe for production).
"""

from __future__ import annotations
import os, struct, itertools, zlib, socket, pathlib
from typing import Optional

try:
    from Crypto.Cipher import AES  # pycryptodome
except ImportError as _e:
    raise ImportError("pycryptodome is required: pip install pycryptodome") from _e

# ─────────────────────────────────────────────────────────── constants
TYPE_GAME = 1   # board updates, results, incoming shots, etc.
TYPE_CHAT = 2   # instant‑messaging text
TYPE_CTRL = 3   # control / info (WELCOME, ERROR, WAIT, etc.)

_HEADER_FMT = "<B I H Q"       # type (1) | seq (4) | len (2) | nonce (8)
_CRC_FMT    = "<I"             # crc32 (4)
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # = 15 bytes
_LEN_PREFIX_FMT = "<H"         # 2‑byte length prefix (framing)

# ─────────────────────────────────────────────────────────── key & cipher helpers
_KEY_PATH = pathlib.Path("shared.key")
if _KEY_PATH.exists() and _KEY_PATH.stat().st_size == 32:
    SECRET_KEY = _KEY_PATH.read_bytes()
else:
    # 32‑byte hard‑coded fallback (for demo only!)
    SECRET_KEY = (b"\x13\xfe\x8d\xa1" * 8)[:32]

assert len(SECRET_KEY) == 32, "AES‑256 key must be 32 bytes"


def _make_cipher(nonce_int: int):
    """Return AES‑CTR cipher object using 64‑bit nonce parameter."""
    nonce_bytes = nonce_int.to_bytes(8, "little")
    return AES.new(SECRET_KEY, AES.MODE_CTR, nonce=nonce_bytes)


def _crc32(data: bytes) -> int:
    """Return unsigned CRC‑32 of *data* (matching zlib's output)."""
    return zlib.crc32(data) & 0xFFFFFFFF

# ─────────────────────────────────────────────────────────── Packet class
class Packet:
    """Represents a single encrypted frame."""

    __slots__ = ("type", "seq", "nonce", "data")

    def __init__(self, ptype: int, seq: int, nonce: int, payload: bytes):
        self.type: int = ptype
        self.seq: int = seq
        self.nonce: int = nonce
        self.data: bytes = payload  # plaintext in constructor

    # ––––––––––––––––––––––––––––– encode / decode
    def encode(self) -> bytes:
        """Return wire‑encoded frame (with ciphertext & CRC)."""
        cipher = _make_cipher(self.nonce)
        ct = cipher.encrypt(self.data)
        header = struct.pack(_HEADER_FMT, self.type, self.seq, len(ct), self.nonce)
        crc = struct.pack(_CRC_FMT, _crc32(header + ct))
        return header + ct + crc

    @staticmethod
    def decode(buf: bytes, last_seq_seen: int) -> "Packet":
        """Validate CRC, anti‑replay, decrypt and return Packet object.

        *last_seq_seen* is the highest seq accepted so far on this connection.
        If the incoming seq <= last_seq_seen, raise ValueError("replay / old seq").
        """
        if len(buf) < _HEADER_SIZE + 4:
            raise ValueError("frame too short")

        ptype, seq, length, nonce = struct.unpack_from(_HEADER_FMT, buf, 0)
        if seq <= last_seq_seen:
            raise ValueError("replay / old seq")

        ct_start = _HEADER_SIZE
        ct_end = ct_start + length
        if len(buf) != ct_end + 4:
            raise ValueError("size mismatch")

        ct = buf[ct_start:ct_end]
        (rx_crc,) = struct.unpack_from(_CRC_FMT, buf, ct_end)
        if _crc32(buf[:ct_end]) != rx_crc:
            raise ValueError("bad crc")

        plain = _make_cipher(nonce).decrypt(ct)
        return Packet(ptype, seq, nonce, plain)

    # convenience string‑representation
    def __repr__(self):
        return f"Packet(type={self.type}, seq={self.seq}, nonce={self.nonce}, data_len={len(self.data)})"

# ─────────────────────────────────────────────────────────── generators

def seq_gen():
    """Return monotonic counter generator: 1,2,3,…"""
    return itertools.count(1)


def nonce_gen():
    """Return generator producing unique 64‑bit nonces (counter seeded rand)."""
    seed = int.from_bytes(os.urandom(8), "little")
    return itertools.count(seed)

# ─────────────────────────────────────────────────────────── socket helpers

def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly *n* bytes or return None when connection closed."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def send_pkt(sock: socket.socket, pkt: Packet):
    """Send *pkt* over TCP socket with 2‑byte length prefix."""
    data = pkt.encode()
    sock.sendall(struct.pack(_LEN_PREFIX_FMT, len(data)) + data)


def recv_pkt(sock: socket.socket, last_seq_seen: int, timeout: float | None = None) -> Optional[Packet]:
    """Receive & return next Packet (or None on timeout/EOF)."""
    sock.settimeout(timeout)
    try:
        length_raw = _recv_exact(sock, struct.calcsize(_LEN_PREFIX_FMT))
        if not length_raw:
            return None
        (frame_len,) = struct.unpack(_LEN_PREFIX_FMT, length_raw)
        frame = _recv_exact(sock, frame_len)
        if frame is None:
            return None
        return Packet.decode(frame, last_seq_seen)
    except (socket.timeout, ConnectionResetError, BrokenPipeError):
        return None
    except ValueError:
        # bad crc / replay / size mismatch – drop silently
        return None
