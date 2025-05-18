"""
client.py

Connects to a Battleship server which runs the single-player game.
Simply pipes user input to the server, and prints all server responses.

TODO: Fix the message synchronization issue using concurrency (Tier 1, item 1).
"""

import socket
import threading
import sys

HOST = "127.0.0.1"
PORT = 5000
running = True 


def receiver(rfile):
    """Continuously print everything that arrives from the server."""
    global running
    try:
        while running:
            line = rfile.readline()
            if not line:
                print("\n[INFO] Server closed the connection.")
                running = False
                break
            line = line.rstrip("\n")
            if line == "GRID":
                print("\n[Board]")
                while True:
                    bl = rfile.readline()
                    if not bl or bl.strip() == "":
                        break
                    print(bl.rstrip("\n"))
            else:
                print(line)
    except Exception as e:
        if running:
            print(f"\n[ERROR] Receiver thread: {e}")
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
