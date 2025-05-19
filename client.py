import socket
import threading
import protocol_enc as proto

HOST, PORT = "127.0.0.1", 5000
running = True
last_seq_rx = 0    # ← global tracker seq masuk

def recv_loop(sock):
    global running, last_seq_rx
    while running:
        pkt = proto.recv_pkt(sock, last_seq_rx)
        if pkt is None:
            running = False
            break

        # update seq-tracker sebelum decode
        last_seq_rx = pkt.seq

        # tampilkan sesuai tipe
        if pkt.type == proto.TYPE_GAME:
            print(pkt.data.decode())
        elif pkt.type == proto.TYPE_CHAT:
            print(f"[CHAT] {pkt.data.decode()}")
        else:  # TYPE_CTRL
            print(f"[INFO] {pkt.data.decode()}")

    print("## Disconnected")

def main():
    global running, last_seq_rx
    username = input("Username: ").strip() or "anon"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    # kirim username dulu (plain)
    sock.sendall((username + "\n").encode())

    # init generators
    seqtx   = proto.seq_gen()
    noncetx = proto.nonce_gen()
    # last_seq_rx sudah 0 dari atas

    # start receiver thread
    threading.Thread(target=recv_loop, args=(sock,), daemon=True).start()

    # beri waktu singkat agar pesan RECONNECTED/boards bisa muncul
    # (opsional, tapi membantu)
    threading.Event().wait(0.1)

    try:
        while running:
            cmd = input(">> ").strip()
            if not running:
                break

            if cmd.startswith("/chat "):
                ptype  = proto.TYPE_CHAT
                payload = cmd[6:].encode()
            else:
                ptype  = proto.TYPE_GAME
                payload = cmd.encode()

            pkt = proto.Packet(ptype, next(seqtx), next(noncetx), payload)
            proto.send_pkt(sock, pkt)

            if cmd.lower() == "quit":
                break

    except KeyboardInterrupt:
        pass
    finally:
        running = False
        try: sock.shutdown(socket.SHUT_RDWR)
        except: pass
        sock.close()

if __name__ == "__main__":
    main()
