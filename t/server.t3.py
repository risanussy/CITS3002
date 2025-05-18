"""
Battleship server – Tier-3 compliant
------------------------------------
• Banyak klien sekaligus, spectator real-time
• Re-connect player ≤ 60 s
• Lobby FIFO → tiap akhir match otomatis ganti pemain
"""

import socket
import threading
import time
from queue import Queue, Empty

from battleship import Board, SHIPS, BOARD_SIZE, safe_parse_coordinate

HOST, PORT = "0.0.0.0", 5000
TURN_TIMEOUT       = 30          # detik/giliran
RECONNECT_WINDOW   = 60          # detik tunggu reconnect
LOBBY_PING_PERIOD  = 10          # detik kirim ulang pesan lobby

# ───────────────────────────────────────────────────────── Player ──
class Player:
    def __init__(self, conn: socket.socket, addr, name: str):
        self.name   = name
        self.conn   = conn
        self.addr   = addr
        self.rfile  = conn.makefile("r")
        self.wfile  = conn.makefile("w", buffering=1)
        self.board  = Board(BOARD_SIZE)
        self.board.place_ships_randomly(SHIPS)
        self.role   = "waiting"      # waiting | player | spectator
        self.alive  = True
        self.in_game = False         # True jika sedang match
        self._lock  = threading.Lock()

    # —— helper kirim
    def send(self, msg: str):
        with self._lock:
            if not self.alive:
                return
            try:
                self.wfile.write(msg + "\n")
                self.wfile.flush()
            except (BrokenPipeError, OSError):
                self.alive = False

    # —— helper baca dg timeout
    def recv(self, timeout=None):
        try:
            self.conn.settimeout(timeout)
            data = self.rfile.readline()
            if not data:
                self.alive = False
                return None
            return data.rstrip("\n")
        except (socket.timeout, OSError):
            return None

    # —— dipanggil ketika re-connect
    def reattach(self, new_conn: socket.socket, addr):
        with self._lock:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn  = new_conn
            self.addr  = addr
            self.rfile = new_conn.makefile("r")
            self.wfile = new_conn.makefile("w", buffering=1)
            self.alive = True

    def close(self):
        with self._lock:
            self.alive = False
            try:
                self.conn.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self.conn.close()
            except Exception:
                pass

# ────────────────────────────────────────── util: kirim papan ──
def send_board(to_player: Player, whose: Player, label: str = ""):
    to_player.send(f"GRID {label}".strip())
    header = "  " + " ".join(str(i + 1).rjust(2) for i in range(whose.board.size))
    to_player.send(header)
    for r in range(whose.board.size):
        row_lbl = chr(ord('A') + r)
        row = " ".join(whose.board.display_grid[r][c] for c in range(whose.board.size))
        to_player.send(f"{row_lbl:2} {row}")
    to_player.send("")

# ────────────────────────────────────────── Game session ──
class GameSession(threading.Thread):
    def __init__(self, p0: Player, p1: Player, spectators: list[Player]):
        super().__init__(daemon=True)
        self.players     = [p0, p1]
        self.spectators  = spectators     # bisa bertambah via add_spectator()
        self._lock       = threading.Lock()
        for p in self.players:
            p.role = "player"
            p.in_game = True
        for s in self.spectators:
            s.role = "spectator"
            s.in_game = True
            self._welcome_spectator(s)

    # —— spectator baru bergabung
    def add_spectator(self, p: Player):
        with self._lock:
            p.role = "spectator"
            p.in_game = True
            self.spectators.append(p)
            self._welcome_spectator(p)

    # —— kirim ucapan sambutan + papan terkini ke spectator baru
    def _welcome_spectator(self, p: Player):
        p.send("SPECTATOR-START – You are now watching the current match.")
        for idx, pl in enumerate(self.players, 1):
            send_board(p, pl, f"PLAYER-{idx}")

    # —— broadcast pesan ke semua orang di session
    def bcast(self, msg: str):
        for p in (*self.players, *self.spectators):
            p.send(msg)

    # —— kirim papan terkini ke semua (players + spectators)
    def push_boards(self):
        send_board(self.players[0], self.players[1])
        send_board(self.players[1], self.players[0])
        for s in self.spectators:
            send_board(s, self.players[0], "PLAYER-1")
            send_board(s, self.players[1], "PLAYER-2")

    # —— run() = loop permainan
    def run(self):
        p0, p1 = self.players
        p0.send("MATCH-START You are Player-1 (first).")
        p1.send("MATCH-START You are Player-2 (second).")
        self.bcast("MATCH-START – 2 players ready, match begins.")
        current = 0

        while True:
            me, enemy = self.players[current], self.players[1 - current]
            self.push_boards()

            me.send(f"YOURTURN (timeout {TURN_TIMEOUT}s) – coordinate or 'quit':")
            enemy.send("WAIT – opponent thinking...")
            for s in self.spectators:
                s.send("WAIT – player thinking...")

            cmd = me.recv(timeout=TURN_TIMEOUT)

            # —— handle disconnect / timeout
            if cmd is None:
                self.bcast(f"NOTICE – {me.name} disconnected. Waiting up to "
                           f"{RECONNECT_WINDOW}s for reconnection...")
                waited = 0
                while waited < RECONNECT_WINDOW and not me.alive:
                    time.sleep(1)
                    waited += 1
                if me.alive:
                    self.bcast(f"NOTICE – {me.name} re-connected. Game resumes.")
                    continue
                else:
                    enemy.send("Opponent failed to reconnect – YOU WIN!")
                    for s in self.spectators:
                        s.send(f"GAME-OVER – {enemy.name} wins (opponent disconnect).")
                    break

            cmd = cmd.strip()
            if cmd.lower() == "quit":
                me.send("You forfeited – bye.")
                enemy.send("Opponent forfeited – YOU WIN!")
                self.bcast(f"GAME-OVER – {enemy.name} wins (forfeit).")
                break

            try:
                r, c = safe_parse_coordinate(cmd)
            except ValueError as e:
                me.send(f"Invalid coordinate: {e}")
                continue

            result, sunk = enemy.board.fire_at(r, c)
            if result == "already_shot":
                me.send("Already shot there – choose again.")
                continue

            tag = "HIT" if result == "hit" else "MISS"
            extra = f" and sank the {sunk}" if sunk else ""
            me.send(f"RESULT {tag}{extra}")
            enemy.send(f"INCOMING {cmd} – {tag}{extra}")
            for s in self.spectators:
                s.send(f"UPDATE {me.name} fired {cmd} – {tag}{extra}")

            if enemy.board.all_ships_sunk():
                me.send("All enemy ships sunk – YOU WIN!")
                enemy.send("All your ships are sunk – YOU LOSE!")
                for s in self.spectators:
                    s.send(f"GAME-OVER – {me.name} wins (all ships sunk).")
                break

            current = 1 - current  # giliran ganti

        # —— game selesai: bersihkan status
        for p in (*self.players, *self.spectators):
            p.in_game = False
            p.role = "waiting"
        # pemain/spectator yang masih terkoneksi tetap hidup – lobby manager
        # akan memproses mereka untuk match berikutnya.

# ────────────────────────────────────────── Lobby Manager ──
class LobbyManager:
    def __init__(self):
        self.waiting: "Queue[Player]" = Queue()
        self.active_session: GameSession | None = None
        self.players_by_name: dict[str, Player] = {}
        self._lock = threading.Lock()

    def listener(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((HOST, PORT))
            s.listen()
            print(f"[INFO] Server listening on {HOST}:{PORT}")

            while True:
                conn, addr = s.accept()
                rfile = conn.makefile("r")
                conn.settimeout(5)                 # tunggu username max 5 s
                try:
                    first = rfile.readline()
                    username = first.rstrip("\n").strip() if first else ""
                except socket.timeout:
                    username = ""                   # tak ada baris dikirim
                conn.settimeout(None)

                if not username:
                    username = f"guest-{addr[0]}:{addr[1]}"

                threading.Thread(
                    target=self.handle_new_conn,
                    args=(conn, addr, username),
                    daemon=True
                ).start()

    # —— proses koneksi baru (atau re-attach)
    def handle_new_conn(self, conn, addr, username: str):
        with self._lock:
            # re-attach?
            if username in self.players_by_name:
                old_p = self.players_by_name[username]
                if not old_p.alive and old_p.in_game:
                    old_p.reattach(conn, addr)
                    old_p.send("RECONNECTED – welcome back!")
                    return
            # username baru
            p = Player(conn, addr, username)
            self.players_by_name[username] = p
            self.waiting.put(p)
            p.send("CONNECTED – waiting for next match...")

            # keep-alive lobby ping
            threading.Thread(target=self.lobby_ping, args=(p,), daemon=True).start()

            # bila ada match berjalan → jadikan spectator
            if self.active_session is not None:
                self.active_session.add_spectator(p)

    # —— kirim pesan lobby tiap 10s
    def lobby_ping(self, p: Player):
        while p.alive and not p.in_game:
            p.send("LOBBY – waiting for your turn...")
            time.sleep(LOBBY_PING_PERIOD)

    # —— loop utama: pasang pemain baru / start match
    def run(self):
        threading.Thread(target=self.listener, daemon=True).start()
        while True:
            # pastikan ada session aktif; jika tidak, coba buat baru
            if self.active_session is None or not self.active_session.is_alive():
                self.active_session = None
                players = []
                while len(players) < 2:
                    try:
                        cand = self.waiting.get(timeout=0.5)
                    except Empty:
                        cand = None
                    if cand and cand.alive and not cand.in_game:
                        players.append(cand)
                spectators = [p for p in list(self.players_by_name.values())
                              if p.alive and not p.in_game and p not in players]
                self.active_session = GameSession(players[0], players[1], spectators)
                self.active_session.start()
            time.sleep(1)   # hindari busy-loop

# ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    LobbyManager().run()
