"""
Tier-2 compliant Battleship server.
• Bisa banyak sesi, lobby FIFO
• Timeout 30 s per giliran, DC ⇒ forfeit
"""

import socket, threading, time
from queue import Queue, Empty

from battleship import Board, SHIPS, BOARD_SIZE, safe_parse_coordinate

HOST, PORT = "0.0.0.0", 5000
TURN_TIMEOUT = 30
LOBBY_MSG_INT = 10

# ───────────────────────────────────────────────────────── Player ──
class Player:
    def __init__(self, conn, addr, name):
        self.conn, self.addr, self.name = conn, addr, name
        self.rfile = conn.makefile("r")
        self.wfile = conn.makefile("w", buffering=1)
        self.board = Board(BOARD_SIZE)
        self.board.place_ships_randomly(SHIPS)
        self.alive = True
        self._lock = threading.Lock()

    def send(self, msg: str):
        with self._lock:
            if not self.alive:
                return
            try:
                self.wfile.write(msg + "\n")
                self.wfile.flush()
            except (BrokenPipeError, OSError):
                self.alive = False

    def recv(self, timeout=None):
        self.conn.settimeout(timeout)
        try:
            data = self.rfile.readline()
            if not data:
                self.alive = False
                return None
            return data.rstrip("\n")
        except (socket.timeout, OSError):
            return None

    def close(self):
        self.alive = False
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass

# ─────────────────────────────────────────────────────── Helpers ──
def send_board(to_player: Player, whose: Player):
    """Kirim papan `whose` (display_grid) ke `to_player`."""
    to_player.send("GRID")
    header = "  " + " ".join(str(i + 1).rjust(2) for i in range(whose.board.size))
    to_player.send(header)
    for r in range(whose.board.size):
        label = chr(ord('A') + r)
        row = " ".join(whose.board.display_grid[r][c] for c in range(whose.board.size))
        to_player.send(f"{label:2} {row}")
    to_player.send("")

# ─────────────────────────────────────────────────── Game session ──
def game_session(p0: Player, p1: Player):
    players = [p0, p1]
    p0.send("MATCH-START You are Player-1 (first)")
    p1.send("MATCH-START You are Player-2 (second)")

    current = 0
    while True:
        me, enemy = players[current], players[1 - current]

        # ⚓-- kirim papan
        send_board(me, enemy)    # pemain lihat papan lawan
        send_board(enemy, me)    # vice-versa

        me.send(f"YOURTURN (timeout {TURN_TIMEOUT}s) – coordinate or 'quit':")
        enemy.send("WAIT – opponent thinking...")

        cmd = me.recv(timeout=TURN_TIMEOUT)
        if cmd is None:
            me.send("Timeout / disconnect – you forfeit.")
            enemy.send("Opponent left – you win!")
            break

        cmd = cmd.strip()
        if cmd.lower() == "quit":
            me.send("You forfeited – bye.")
            enemy.send("Opponent forfeited – you win!")
            break

        try:
            r, c = safe_parse_coordinate(cmd)
        except ValueError as e:
            me.send(f"Invalid coordinate: {e}")
            continue        # ulangi giliran

        res, sunk = enemy.board.fire_at(r, c)
        if res == "already_shot":
            me.send("Already shot there – choose again.")
            continue

        tag = "HIT" if res == "hit" else "MISS"
        extra = f" and sank the {sunk}" if sunk else ""
        me.send(f"RESULT {tag}{extra}")
        enemy.send(f"INCOMING {cmd} – {tag}{extra}")

        if enemy.board.all_ships_sunk():
            me.send("All enemy ships sunk – YOU WIN!")
            enemy.send("All your ships are sunk – YOU LOSE!")
            break

        current = 1 - current   # ganti giliran

    for p in players:
        p.close()

# ─────────────────────────────────────────────────── Lobby/server ──
def lobby_manager():
    lobby: Queue[Player] = Queue()

    def listener():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((HOST, PORT))
            s.listen()
            print(f"[INFO] Server listening on {HOST}:{PORT}")
            while True:
                conn, addr = s.accept()
                player = Player(conn, addr, f"{addr[0]}:{addr[1]}")
                player.send("CONNECTED – waiting in lobby…")
                lobby.put(player)

    threading.Thread(target=listener, daemon=True).start()

    while True:
        p0 = lobby.get()
        if not p0.alive:
            continue

        # pasang notifikasi lobby tiap 10 s
        def keep_alive(p):
            while p.alive:
                p.send("LOBBY – still waiting for opponent…")
                time.sleep(LOBBY_MSG_INT)
        threading.Thread(target=keep_alive, args=(p0,), daemon=True).start()

        # cari partner
        p1 = None
        while not p1:
            try:
                cand = lobby.get(timeout=0.5)
                if cand.alive:
                    p1 = cand
            except Empty:
                if not p0.alive:
                    p0 = None
                    break
        if p0 is None:
            continue

        p0.send("MATCH FOUND – starting…")
        p1.send("MATCH FOUND – starting…")
        threading.Thread(target=game_session, args=(p0, p1), daemon=True).start()

if __name__ == "__main__":
    lobby_manager()
