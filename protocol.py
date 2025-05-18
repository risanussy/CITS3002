"""
Low-level framing untuk Tier-4 (T4.1)
------------------------------------
Format paket (little-endian):
 0     : uint8   type      (1 = GAME, 2 = CHAT, 3 = CTRL)
 1..4  : uint32  seq       (increment per koneksi)
 5..6  : uint16  length    (panjang payload)
 7..(7+length-1) : payload (bytes)
 ...4  : uint32  crc32     (CRC-32 atas header+payload)
Total = 11 + len(payload) bytes
"""

import struct, zlib, itertools, socket

# ───── konstanta type
TYPE_GAME = 1   # event game, board, dll
TYPE_CHAT = 2   # pesan chat
TYPE_CTRL = 3   # info kontrol (WELCOME, ERROR, dsb.)

_HEADER = "<B I H"     # type, seq, len   → 1+4+2 = 7 byte
_CRC_FMT = "<I"        # crc32            → 4 byte

def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF

class Packet:
    def __init__(self, type_: int, seq: int, payload: bytes):
        self.type  = type_
        self.seq   = seq
        self.data  = payload

    # ——— serialize
    def encode(self) -> bytes:
        hdr = struct.pack(_HEADER, self.type, self.seq, len(self.data))
        crc = struct.pack(_CRC_FMT, _crc32(hdr + self.data))
        return hdr + self.data + crc

    # ——— parse from buffer (raises ValueError on bad CRC)
    @staticmethod
    def decode(buf: bytes):
        if len(buf) < 11:
            raise ValueError("frame too short")
        type_, seq, length = struct.unpack_from(_HEADER, buf, 0)
        if len(buf) != 11 + length:
            raise ValueError("size mismatch")
        payload = buf[7:7 + length]
        (rx_crc,) = struct.unpack_from(_CRC_FMT, buf, 7 + length)
        if _crc32(buf[:7 + length]) != rx_crc:
            raise ValueError("bad crc")
        return Packet(type_, seq, payload)

# ───── helper kirim/terima via socket (blocking, kecil & simpel)
def send_pkt(sock: socket.socket, pkt: Packet):
    data = pkt.encode()
    # prepend 2-byte length untuk framing di TCP stream
    sock.sendall(struct.pack("<H", len(data)) + data)

def recv_pkt(sock: socket.socket, timeout=None) -> Packet | None:
    sock.settimeout(timeout)
    try:
        # read 2-byte length prefix
        length_raw = _recv_exact(sock, 2)
        if not length_raw:
            return None
        (frame_len,) = struct.unpack("<H", length_raw)
        frame = _recv_exact(sock, frame_len)
        if frame is None:
            return None
        return Packet.decode(frame)
    except (socket.timeout, ConnectionResetError, BrokenPipeError):
        return None

def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    chunks = []
    left = n
    while left:
        chunk = sock.recv(left)
        if not chunk:
            return None
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)

# ───── generator seq# per koneksi
def seq_gen():
    return itertools.count(1)
