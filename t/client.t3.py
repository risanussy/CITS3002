"""
Battleship client – Tier-3 aware
• Pertama kali diminta username → kirim ke server
• Auto-reconnect manual: jalankan ulang dengan username sama
• Kalau Anda spectator, tembakan Anda akan di-abaikan
"""

import socket
import threading
import sys

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
            if line.startswith("GRID"):
                label = line[4:].strip()
                if label:
                    print(f"\n=== Board ({label}) ===")
                else:
                    print("\n=== Board ===")
                while True:
                    row = rfile.readline()
                    if not row or row.strip() == "":
                        break
                    print(row.rstrip("\n"))
            else:
                print(line)
    except Exception as e:
        if running:
            print(f"[ERROR] Receiver: {e}")
    finally:
        running = False


def main():
    global running
    username = input("Enter username (use the same one to reconnect): ").strip() or "anon"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        wfile = s.makefile("w", buffering=1)
        rfile = s.makefile("r")

        # kirim username sebagai baris pertama
        wfile.write(username + "\n")
        wfile.flush()

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
