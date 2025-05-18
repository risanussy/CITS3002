import socket, threading, sys
import protocol as proto

HOST, PORT = "127.0.0.1", 5000
running = True

# ───────────────────────────────────────── receiver ──
def recv_loop(sock):
    global running                          # ⬅️ taruh di sini
    while running:
        pkt = proto.recv_pkt(sock)
        if pkt is None:
            break
        if pkt.type == proto.TYPE_GAME:
            print(pkt.data.decode())
        elif pkt.type == proto.TYPE_CHAT:
            print(f"[CHAT] {pkt.data.decode()}")
        else:                               # TYPE_CTRL
            print(f"[INFO] {pkt.data.decode()}")
    print("## Disconnected")
    running = False

# ───────────────────────────────────────── main ──
def main():
    global running
    username = input("Username: ").strip() or "anon"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    sock.sendall((username + "\n").encode())       # baris pertama: username

    threading.Thread(target=recv_loop, args=(sock,), daemon=True).start()
    seqtx = proto.seq_gen()

    try:
        while running:
            msg = input(">> ").strip()
            if not running:
                break
            if msg.startswith("/chat "):
                payload = msg[6:].encode()
                pkt_type = proto.TYPE_CHAT
            else:
                payload = msg.encode()
                pkt_type = proto.TYPE_GAME
            proto.send_pkt(sock, proto.Packet(pkt_type, next(seqtx), payload))
            if msg.lower() == "quit":
                break
    except KeyboardInterrupt:
        pass
    finally:
        running = False
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        sock.close()

if __name__ == "__main__":
    main()
