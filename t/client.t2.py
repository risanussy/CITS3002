"""
Tier-2 ready Battleship client.
Sekarang:
 • Terus menerus menampilkan board & pesan server.
 • Putus koneksi / Ctrl-C ⇒ keluar rapi.
"""

import socket, threading, sys

HOST, PORT = "127.0.0.1", 5000
running = True


def receiver(rfile):
    global running
    try:
        while running:
            line = rfile.readline()
            if not line:
                print("\n[INFO] Connection closed by server.")
                running = False
                break
            line = line.rstrip("\n")
            if line == "GRID":
                print("\n=== Board ===")
                while True:
                    grid_line = rfile.readline()
                    if not grid_line or grid_line.strip() == "":
                        break
                    print(grid_line.rstrip("\n"))
            else:
                print(line)
    except Exception as e:
        if running:
            print(f"[ERROR] Receiver: {e}")
    finally:
        running = False


def main():
    global running
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        rfile = s.makefile("r")
        wfile = s.makefile("w", buffering=1)

        threading.Thread(target=receiver, args=(rfile,), daemon=True).start()

        try:
            while running:
                try:
                    user = input(">> ").strip()
                except EOFError:
                    user = "quit"
                if not running:
                    break
                wfile.write(user + "\n")
                wfile.flush()
                if user.lower() == "quit":
                    running = False
        except KeyboardInterrupt:
            print("\n[INFO] Closing client.")
        finally:
            running = False
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass


if __name__ == "__main__":
    main()
