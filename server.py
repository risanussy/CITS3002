"""
Battleship Server – Tier-4.3 Complete (Fixed Reconnect)
• AES-CTR + CRC-32 (protocol_enc)
• Instant chat (TYPE_CHAT)
• Reconnect ≤ 60s (in-lobby & in-match)
• Spectators, lobby FIFO, multiple matches
"""
import socket
import threading
import time
from queue import Queue, Empty

from battleship import BOARD_SIZE, Board, SHIPS, safe_parse_coordinate
import protocol_enc as proto

HOST, PORT = "0.0.0.0", 5000
TURN_TIMEOUT = 30      # seconds per turn
RECONN_WAIT  = 60      # seconds wait for reconnect
PING_LOBBY   = 10      # seconds lobby ping

# ───── Player Definition ──────────
class Player:
    def __init__(self, sock, addr, name: str):
        self.sock        = sock
        self.addr        = addr
        self.name        = name
        self.seqtx       = proto.seq_gen()
        self.noncetx     = proto.nonce_gen()
        self.last_seq_rx = 0
        self.board       = Board(BOARD_SIZE)
        self.board.place_ships_randomly(SHIPS)
        self.role        = "waiting"
        self.in_game     = False
        self.alive       = True
        self.lock        = threading.Lock()

    def send(self, ptype: int, text: str):
        pkt = proto.Packet(ptype,
                           next(self.seqtx),
                           next(self.noncetx),
                           text.encode())
        with self.lock:
            try:
                proto.send_pkt(self.sock, pkt)
            except:
                self.alive = False

    def recv(self, timeout=None):
        try:
            pkt = proto.recv_pkt(self.sock, self.last_seq_rx, timeout)
        except OSError:
            self.alive = False
            return None
        if pkt is None:
            self.alive = False
            return None
        self.last_seq_rx = pkt.seq
        return pkt.type, pkt.data.decode(errors="replace")

    def reattach(self, new_sock):
        with self.lock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
            except:
                pass
            self.sock        = new_sock
            self.seqtx       = proto.seq_gen()
            self.noncetx     = proto.nonce_gen()
            self.last_seq_rx = 0
            self.alive       = True

    def close(self):
        with self.lock:
            self.alive = False
            try: self.sock.shutdown(socket.SHUT_RDWR)
            except: pass
            try: self.sock.close()
            except: pass

# ───── ASCII Board to Text ──────── to Text ────────
def board_ascii(board: Board) -> str:
    lines = []
    header = "  " + " ".join(str(i+1).rjust(2) for i in range(board.size))
    lines.append(header)
    for r in range(board.size):
        lbl = chr(ord('A') + r)
        row = " ".join(board.display_grid[r][c] for c in range(board.size))
        lines.append(f"{lbl:2} {row}")
    return "\n".join(lines)

# ───── Game Session ──────────────
class GameSession(threading.Thread):
    def __init__(self, p0: Player, p1: Player, spectators: list[Player]):
        super().__init__(daemon=True)
        self.players    = [p0, p1]
        self.spectators = spectators
        self.current    = 0
        # mark roles
        for p in self.players:
            p.role    = "player"
            p.in_game = True
        for s in self.spectators:
            s.role    = "spectator"
            s.in_game = True
            self._welcome_spec(s)
        self._lock = threading.Lock()

    def _welcome_spec(self, p: Player):
        p.send(proto.TYPE_CTRL, "SPECTATOR-START")
        p.send(proto.TYPE_GAME, f"PLAYER-1\n{board_ascii(self.players[0].board)}")
        p.send(proto.TYPE_GAME, f"PLAYER-2\n{board_ascii(self.players[1].board)}")

    def add_spectator(self, p: Player):
        with self._lock:
            self.spectators.append(p)
            p.role    = "spectator"
            p.in_game = True
            self._welcome_spec(p)

    def bcast(self, ptype: int, msg: str):
        for p in (*self.players, *self.spectators):
            p.send(ptype, msg)

    def push_boards(self):
        b0 = board_ascii(self.players[0].board)
        b1 = board_ascii(self.players[1].board)
        # Player-1
        self.players[0].send(proto.TYPE_GAME, f"PLAYER-1\n{b0}")
        # Player-2
        self.players[1].send(proto.TYPE_GAME, f"PLAYER-2\n{b1}")
        # Spectators
        for s in self.spectators:
            s.send(proto.TYPE_GAME, f"PLAYER-1\n{b0}")
            s.send(proto.TYPE_GAME, f"PLAYER-2\n{b1}")

    def _drain_chat(self):
        # gather chat from opponent & spectators
        peers = [self.players[1-self.current], *self.spectators]
        for p in peers:
            if not p.alive: continue
            tp = p.recv(timeout=0.01)
            if tp and tp[0] == proto.TYPE_CHAT:
                self.bcast(proto.TYPE_CHAT, f"{p.name}: {tp[1]}")

    def _handle_dc(self, leaver: Player, opponent: Player) -> bool:
        self.bcast(proto.TYPE_CTRL,
                   f"DC {leaver.name} — waiting {RECONN_WAIT}s to reconnect…")
        waited = 0
        while waited < RECONN_WAIT and not leaver.alive:
            time.sleep(1); waited += 1
        if leaver.alive:
            leaver.send(proto.TYPE_CTRL, "RECONNECTED")
            self.push_boards()
            # prompt turn
            if self.players[self.current] is leaver:
                leaver.send(proto.TYPE_CTRL, f"YOURTURN {TURN_TIMEOUT}")
                opponent.send(proto.TYPE_CTRL, "WAIT - opponent thinking…")
            else:
                opponent.send(proto.TYPE_CTRL, f"YOURTURN {TURN_TIMEOUT}")
                leaver.send(proto.TYPE_CTRL,   "WAIT - opponent thinking…")
            self.bcast(proto.TYPE_CTRL, f"REJOIN {leaver.name} — game resumes.")
            return False
        # timeout
        opponent.send(proto.TYPE_CTRL, "WIN")
        self.bcast(proto.TYPE_CTRL,
                   f"{opponent.name} wins (disconnect).")
        return True

    def run(self):
        p0, p1 = self.players
        p0.send(proto.TYPE_CTRL, "MATCH-START FIRST")
        p1.send(proto.TYPE_CTRL, "MATCH-START SECOND")
        self.bcast(proto.TYPE_CTRL, "MATCH-START")
        self.current = 0
        while True:
            me = self.players[self.current]
            en = self.players[1-self.current]
            self._drain_chat()
            self.push_boards()
            me.send(proto.TYPE_CTRL, f"YOURTURN {TURN_TIMEOUT}")
            en.send(proto.TYPE_CTRL, "WAIT - opponent thinking…")
            for s in self.spectators:
                s.send(proto.TYPE_CTRL, "WAIT - opponent thinking…")
            tp = me.recv(timeout=TURN_TIMEOUT)
            if tp is None:
                if self._handle_dc(me, en): break
                else: continue
            ptype, cmd = tp
            if ptype == proto.TYPE_CHAT:
                self.bcast(proto.TYPE_CHAT, f"{me.name}: {cmd}")
                continue
            if ptype != proto.TYPE_GAME:
                me.send(proto.TYPE_CTRL, "ERROR unexpected packet")
                continue
            if cmd.lower() == "quit":
                me.send(proto.TYPE_CTRL, "FORFEIT")
                en.send(proto.TYPE_CTRL, "WIN")
                self.bcast(proto.TYPE_CTRL, f"{en.name} wins (forfeit)")
                break
            try:
                r,c = safe_parse_coordinate(cmd)
            except ValueError as e:
                me.send(proto.TYPE_CTRL, f"ERROR {e}")
                continue
            res, sunk = en.board.fire_at(r,c)
            if res == "already_shot":
                me.send(proto.TYPE_CTRL, "ERROR already_shot")
                continue
            tag = "HIT" if res == "hit" else "MISS"
            extra = f" sunk {sunk}" if sunk else ""
            me.send(proto.TYPE_GAME, f"RESULT {tag}{extra}")
            en.send(proto.TYPE_GAME, f"INCOMING {cmd} {tag}{extra}")
            self.bcast(proto.TYPE_GAME, f"{me.name}→{cmd} {tag}{extra}")
            if en.board.all_ships_sunk():
                me.send(proto.TYPE_CTRL, "WIN")
                en.send(proto.TYPE_CTRL, "LOSE")
                self.bcast(proto.TYPE_CTRL, f"{me.name} wins (all ships sunk)")
                break
            self.current = 1 - self.current
        # cleanup after match
        for p in (*self.players, *self.spectators):
            p.role    = "waiting"
            p.in_game = False

# ───── Lobby Manager ─────────────
class Lobby:
    def __init__(self):
        self.waiting = Queue()
        self.by_name = {}
        self.session = None
        threading.Thread(target=self._listener, daemon=True).start()

    def _listener(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((HOST, PORT))
            s.listen()
            print(f"[INFO] Listening on {HOST}:{PORT}")
            while True:
                conn, addr = s.accept()
                rfile = conn.makefile("r", buffering=1, newline="\n")
                name = rfile.readline(64).rstrip("\n") or f"guest-{addr[0]}:{addr[1]}"
                self._attach(conn, addr, name)

    def _attach(self, conn, addr, name):
        # existing user?
        if name in self.by_name:
            old = self.by_name[name]
            # in-match reconnect
            if old.in_game:
                old.reattach(conn)
                print(f"[INFO] Reattach in-match: {name}")
                old.send(proto.TYPE_CTRL, "RECONNECTED")
                if self.session and self.session.is_alive():
                    self.session.push_boards()
                    old.send(proto.TYPE_CTRL, f"REJOIN {name} — game resumes.")
                return
            # lobby reconnect
            old.reattach(conn)
            print(f"[INFO] Reattach in-lobby: {name}")
            old.send(proto.TYPE_CTRL, "RECONNECTED")
            old.send(proto.TYPE_CTRL, "CONNECTED waiting")
            self.waiting.put(old)
            return
        # new registration
        p = Player(conn, addr, name)
        self.by_name[name] = p
        threading.Thread(target=self._lobby_ping, args=(p,), daemon=True).start()
        if self.session and self.session.is_alive():
            self.session.add_spectator(p)
        else:
            p.send(proto.TYPE_CTRL, "CONNECTED waiting")
            self.waiting.put(p)

    def _lobby_ping(self, p: Player):
        while p.alive and not p.in_game:
            p.send(proto.TYPE_CTRL, "LOBBY")
            time.sleep(PING_LOBBY)

    def run(self):
        while True:
            # start new match if none
            if not (self.session and self.session.is_alive()):
                players = []
                while len(players) < 2:
                    try:
                        cand = self.waiting.get(timeout=0.5)
                    except Empty:
                        cand = None
                    if cand and cand.alive and not cand.in_game:
                        players.append(cand)
                specs = [pl for pl in self.by_name.values()
                         if pl.alive and not pl.in_game and pl not in players]
                self.session = GameSession(players[0], players[1], specs)
                self.session.start()
            time.sleep(1)

if __name__ == "__main__":
    Lobby().run()
