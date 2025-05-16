"""
server.py

Serves a single-player Battleship session to one connected client.
Game logic is handled entirely on the server using battleship.py.
Client sends FIRE commands, and receives game feedback.

TODO: For Tier 1, item 1, you don't need to modify this file much. 
The core issue is in how the client handles incoming messages.
However, if you want to support multiple clients (i.e. progress through further Tiers), you'll need concurrency here too.
"""

import socket
import threading
from queue import Queue, Empty

from battleship import Board, parse_coordinate, SHIPS, BOARD_SIZE

HOST = '127.0.0.1'
PORT = 5000


class Player:
    """Holds per-player state and I/O helpers."""

    def __init__(self, conn: socket.socket, addr: tuple, pid: int):
        self.conn = conn
        self.addr = addr
        self.pid = pid         
        self.rfile = conn.makefile("r")
        self.wfile = conn.makefile("w", buffering=1) 
        self.board = Board(BOARD_SIZE)
        self.board.place_ships_randomly(SHIPS)

    def send(self, msg: str):
        try:
            self.wfile.write(msg + "\n")            
            self.wfile.flush() 
        except BrokenPipeError:
            pass

    def send_board(self, opponent_view=False):
        self.send("GRID")
        grid = (
            self.board.display_grid
            if opponent_view
            else self.board.hidden_grid
        )
        header = "  " + " ".join(str(i + 1).rjust(2) for i in range(self.board.size))
        self.send(header)
        for r in range(self.board.size):
            label = chr(ord("A") + r)
            row = " ".join(grid[r][c] for c in range(self.board.size))
            self.send(f"{label:2} {row}")
        self.send("")


def reader_thread(player: Player, q: Queue):
    """
    Reads a line from this player's socket and puts (pid, text) on the shared queue.
    Dies when socket closes.
    """
    while True:
        line = player.rfile.readline()
        if not line:
            q.put((player.pid, "QUIT"))
            break
        q.put((player.pid, line.strip()))


def main():
    print(f"[INFO] Listening on {HOST}:{PORT}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, PORT))
        s.listen()
        
        players = []
        while len(players) < 2:
            conn, addr = s.accept()
            pid = len(players)
            players.append(Player(conn, addr, pid))
            print(f"[INFO] Player {pid + 1} connected from {addr}")

            players[-1].send(
                f"Welcome, Player {pid + 1}! Waiting for another player..."
            )

        p0, p1 = players
        p0.send("Both players connected – you are Player 1 (goes first).")
        p1.send("Both players connected – you are Player 2 (goes second).")

        q: Queue = Queue()
        for p in players:
            threading.Thread(
                target=reader_thread, args=(p, q), daemon=True
            ).start()

        current = 0 
        game_over = False
        
        while not game_over:
            me = players[current]
            enemy = players[1 - current]
            
            me.send_board(opponent_view=True)
            enemy.send_board(opponent_view=True)

            me.send("YOUR TURN – enter coordinate to fire (e.g. B5) or 'quit':")
            enemy.send("WAIT – opponent is choosing a coordinate...")


            while True:
                try:
                    pid, cmd = q.get(timeout=0.1)
                except Empty:
                    continue
                if pid != current:
                    players[pid].send("It is not your turn.")
                    continue
                cmd = cmd.strip()
                if cmd.lower() == "quit":
                    me.send("You forfeited the game. Goodbye.")
                    enemy.send("Opponent forfeited – you win!")
                    game_over = True
                    break
                try:
                    r, c = parse_coordinate(cmd)
                except Exception:
                    me.send("Invalid coordinate – try again:")
                    continue

                result, sunk_name = enemy.board.fire_at(r, c)
                if result == "already_shot":
                    me.send("Already fired there – choose another:")
                    continue


                tag = "HIT" if result == "hit" else "MISS"
                extra = f" and sank the {sunk_name}" if sunk_name else ""
                me.send(f"RESULT {tag}{extra}")
                enemy.send(f"INCOMING {cmd} – {tag}{extra}")
                
                if enemy.board.all_ships_sunk():
                    me.send("All enemy ships sunk – YOU WIN!")
                    enemy.send("All your ships are sunk – YOU LOSE!")
                    game_over = True
                break

            current = 1 - current
            
        for p in players:
            try:
                p.conn.close()
            except Exception:
                pass
        print("[INFO] Match ended – server ready for new players.")


if __name__ == "__main__":
    main()
