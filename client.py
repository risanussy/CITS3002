import socket, threading
import protocol_enc as proto          # pastikan sudah pakai modul baru

HOST, PORT = "127.0.0.1", 5000
running = True

# ───────────────────────── receiver thread ──
def recv_loop(sock):
    global running, last_seq_rx
    while running:
        pkt = proto.recv_pkt(sock, last_seq_rx)      # ← kirim argumen ini
        if pkt is None:
            running = False
            break
        last_seq_rx = pkt.seq                        # update!
        if pkt.type == proto.TYPE_GAME:
            print(pkt.data.decode())
        elif pkt.type == proto.TYPE_CHAT:
            print(f"[CHAT] {pkt.data.decode()}")
        else:                                        # TYPE_CTRL
            print(f"[INFO] {pkt.data.decode()}")
    print("## Disconnected")

# ───────────────────────── main ──
def main():
    global running, last_seq_rx
    username = input("Username: ").strip() or "anon"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    sock.sendall((username + "\n").encode())

    # generators & trackers
    seqtx       = proto.seq_gen()
    noncetx     = proto.nonce_gen()
    last_seq_rx = 0

    threading.Thread(target=recv_loop, args=(sock,), daemon=True).start()

    try:
        while running:
            txt = input(">> ").strip()
            if not running:
                break
            if txt.startswith("/chat "):
                ptype  = proto.TYPE_CHAT
                payload = txt[6:].encode()
            else:
                ptype  = proto.TYPE_GAME
                payload = txt.encode()
            proto.send_pkt(
                sock,
                proto.Packet(ptype, next(seqtx), next(noncetx), payload)
            )
            if txt.lower() == "quit":
                break
    except KeyboardInterrupt:
        pass
    finally:
        running = False
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except:
            pass
        sock.close()

if __name__ == "__main__":
    main()
