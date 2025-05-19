"""
Battleship Server – Tier-4
•   Custom frame + CRC-32 (protocol.py)
•   Instant messaging (type CHAT)
•   Reconnect ≤ 60 s, spectators, lobby FIFO (masih sama)
"""

import socket, threading, time
from queue import Queue, Empty
from battleship import BOARD_SIZE, Board, SHIPS, safe_parse_coordinate
import protocol as proto                    # ← import frame helpers

HOST, PORT = "0.0.0.0", 5000
TURN_TIMEOUT = 30
RECONN_WAIT  = 60
PING_LOBBY   = 10

# ────────── Player container
class Player:
    def __init__(self, sock: socket.socket, addr, name: str):
        self.sock  = sock
        self.addr  = addr
        self.name  = name
        self.seqtx = proto.seq_gen()        # seq# counter
        self.board = Board(BOARD_SIZE); self.board.place_ships_randomly(SHIPS)
        self.role  = "waiting"              # waiting|player|spectator
        self.in_game = False
        self.alive = True
        self.lock  = threading.Lock()

    # send pkt wrapper
    def send(self, type_, text: str):
        payload = text.encode()
        pkt = proto.Packet(type_, next(self.seqtx), payload)
        with self.lock:
            try:
                proto.send_pkt(self.sock, pkt)
            except Exception:
                self.alive = False

    # recv pkt (returns (type, payload) or None)
    def recv(self, timeout=None):
        pkt = proto.recv_pkt(self.sock, timeout)
        if pkt is None:
            self.alive = False
            return None
        return pkt.type, pkt.data.decode(errors="replace")

    # reconnect: ganti socket
    def reattach(self, new_sock):
        with self.lock:
            try: self.sock.close()
            except: pass
            self.sock  = new_sock
            self.seqtx = proto.seq_gen()
            self.alive = True

    def close(self):
        with self.lock:
            self.alive = False
            try: self.sock.shutdown(socket.SHUT_RDWR)
            except: pass
            try: self.sock.close()
            except: pass

# ────────── helper board → text
def board_ascii(board: Board):
    lines = []
    header = "  " + " ".join(str(i+1).rjust(2) for i in range(board.size))
    lines.append(header)
    for r in range(board.size):
        rowlbl = chr(ord('A')+r)
        row = " ".join(board.display_grid[r][c] for c in range(board.size))
        lines.append(f"{rowlbl:2} {row}")
    return "\n".join(lines)

# ────────────────────────────────────────── GameSession ──
class GameSession(threading.Thread):
    def __init__(self, p0: Player, p1: Player, spectators: list[Player]):
        super().__init__(daemon=True)
        self.players     = [p0, p1]
        self.spectators  = spectators
        for p in self.players:
            p.role = "player";     p.in_game = True
        for s in self.spectators:
            s.role = "spectator";  s.in_game = True
            self._welcome_spec(s)

        self._lock = threading.Lock()  

    # ---------- helper ----------
    def _welcome_spec(self, s: Player):
        s.send(proto.TYPE_CTRL, "SPECTATOR-START")
        s.send(proto.TYPE_GAME, f"PLAYER-1\n{board_ascii(self.players[0].board)}")
        s.send(proto.TYPE_GAME, f"PLAYER-2\n{board_ascii(self.players[1].board)}")
        
    def add_spectator(self, p: Player):
        """
        Dipanggil oleh Lobby ketika ada klien baru bergabung
        sementara match masih berlangsung.
        """
        with self._lock:                   # hindari race-condition
            self.spectators.append(p)
            p.role = "spectator"
            p.in_game = True
            self._welcome_spec(p)          # kirim papan & pesan sambutan


    def bcast(self, ptype, msg: str):
        for pl in (*self.players, *self.spectators):
            pl.send(ptype, msg)

    def push_boards(self):
        for s in self.spectators:
            s.send(proto.TYPE_GAME, f"PLAYER-1\n{board_ascii(self.players[0].board)}")
            s.send(proto.TYPE_GAME, f"PLAYER-2\n{board_ascii(self.players[1].board)}")

    # ---------- NEW: drain_chat ----------
    def _drain_chat(self, peers: list[Player]):
        """Ambil paket CHAT dari peers (lawan + spectator) tanpa blocking."""
        for p in peers:
            if not p.alive:
                continue
            tp = p.recv(timeout=0.01)          # 10 ms non-blocking
            if tp is None:
                continue
            ptype, text = tp
            if ptype == proto.TYPE_CHAT:
                self.bcast(proto.TYPE_CHAT, f"{p.name}: {text}")

    # ---------- tangani disconnect / reconnect ----------
    def _handle_dc(self, leaver: Player, opponent: Player):
        self.bcast(proto.TYPE_CTRL,
                f"DC {leaver.name} — waiting {RECONN_WAIT}s…")

        waited = 0
        while waited < RECONN_WAIT and not leaver.alive:
            time.sleep(1)
            waited += 1

        if leaver.alive:                      # sukses re-connect
            self.bcast(proto.TYPE_CTRL,
                    f"REJOIN {leaver.name} — game resumes.")
            return False                      # JANGAN akhiri game
        else:                                 # gagal
            opponent.send(proto.TYPE_CTRL, "WIN")
            self.bcast(proto.TYPE_CTRL,
                    f"{opponent.name} wins (opponent disconnect).")
            return True                       # game berakhir


    # ---------- main loop ----------
    def run(self):
        p0, p1 = self.players
        p0.send(proto.TYPE_CTRL, "MATCH-START FIRST")
        p1.send(proto.TYPE_CTRL, "MATCH-START SECOND")
        self.bcast(proto.TYPE_CTRL, "MATCH-START")

        current = 0
        while True:
            me, enemy = self.players[current], self.players[1 - current]
            self.push_boards()

            # ── ambil chat dari lawan + semua spectator
            self._drain_chat([enemy, *self.spectators])

            me.send(proto.TYPE_CTRL, f"YOURTURN {TURN_TIMEOUT}")
            enemy.send(proto.TYPE_CTRL, "WAIT")
            for s in self.spectators:
                s.send(proto.TYPE_CTRL, "WAIT")

            tp = me.recv(timeout=TURN_TIMEOUT)
            if tp is None:
                if self._handle_dc(me, enemy):   # True => game selesai
                    break
                else:
                    continue                     # lanjut giliran yg sama

            ptype, cmd = tp

            # pemain yg sedang giliran juga boleh chat
            if ptype == proto.TYPE_CHAT:
                self.bcast(proto.TYPE_CHAT, f"{me.name}: {cmd}")
                continue
            if ptype != proto.TYPE_GAME:
                me.send(proto.TYPE_CTRL, "ERROR unexpected packet")
                continue

            if cmd.lower() == "quit":
                me.send(proto.TYPE_CTRL, "FORFEIT")
                enemy.send(proto.TYPE_CTRL, "WIN")
                self.bcast(proto.TYPE_CTRL,
                           f"{enemy.name} wins (forfeit)")
                break

            try:
                r, c = safe_parse_coordinate(cmd.strip())
            except ValueError as e:
                me.send(proto.TYPE_CTRL, f"ERROR {e}")
                continue

            res, sunk = enemy.board.fire_at(r, c)
            if res == "already_shot":
                me.send(proto.TYPE_CTRL, "ERROR already_shot")
                continue

            tag   = "HIT" if res == "hit" else "MISS"
            extra = f" sunk {sunk}" if sunk else ""
            me.send(proto.TYPE_GAME, f"RESULT {tag}{extra}")
            enemy.send(proto.TYPE_GAME, f"INCOMING {cmd} {tag}{extra}")
            self.bcast(proto.TYPE_GAME,
                       f"{me.name}→{cmd} {tag}{extra}")

            if enemy.board.all_ships_sunk():
                me.send(proto.TYPE_CTRL, "WIN")
                enemy.send(proto.TYPE_CTRL, "LOSE")
                self.bcast(proto.TYPE_CTRL,
                           f"{me.name} wins (all ships sunk)")
                break

            current = 1 - current   # ganti giliran

        # bersihkan status sesudah game
        for p in (*self.players, *self.spectators):
            p.role = "waiting"; p.in_game = False


# ────────── Lobby Manager (minor mod – frame aware & chat pass-thru)
class Lobby:
    def __init__(self):
        self.waiting: Queue[Player] = Queue()
        self.by_name: dict[str, Player] = {}
        self.session: GameSession|None = None
        threading.Thread(target=self._listener, daemon=True).start()

    def _listener(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((HOST, PORT))
            s.listen()
            print(f"[INFO] Listen {HOST}:{PORT}")

            while True:
                sock, addr = s.accept()
                # ① baca 1 baris lengkap (username di-akhiri \n)
                try:
                    rfile = sock.makefile("r", buffering=1, newline="\n")
                    username = rfile.readline(64).rstrip("\n") or \
                            f"guest-{addr[0]}:{addr[1]}"
                except Exception:
                    sock.close()
                    continue

                self._attach(sock, addr, username)

    def _attach(self, sock, addr, name):
        if name in self.by_name:
            old = self.by_name[name]
            if old.in_game:            # pemain masih di match  → re-attach
                old.reattach(sock)
                return
            else:                      # match-nya sudah selesai → buang record lama
                old.close()
                del self.by_name[name]

        # nama baru
        p = Player(sock, addr, name)
        self.by_name[name] = p
        threading.Thread(target=self._lobby_ping, args=(p,), daemon=True).start()

        if self.session and self.session.is_alive():
            self.session.add_spectator(p)     # <- helper supaya welcome & push board
        else:
            p.send(proto.TYPE_CTRL, "CONNECTED waiting")
            self.waiting.put(p)

    def _lobby_ping(self,p):
        while p.alive and not p.in_game:
            p.send(proto.TYPE_CTRL,"LOBBY")
            time.sleep(PING_LOBBY)

    def run(self):
        while True:
            if not (self.session and self.session.is_alive()):
                # build next match
                players=[]
                while len(players)<2:
                    try: cand=self.waiting.get(timeout=0.5)
                    except Empty: cand=None
                    if cand and cand.alive and not cand.in_game: players.append(cand)
                specs=[pl for pl in self.by_name.values()
                       if pl.alive and not pl.in_game and pl not in players]
                self.session = GameSession(players[0], players[1], specs)
                self.session.start()
            time.sleep(1)

if __name__=="__main__":
    Lobby().run()
