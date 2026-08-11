#!/usr/bin/env python3
"""Save a Lovelace dashboard config over Home Assistant's websocket API.

Editing .storage directly does not work while HA is running: LovelaceStorage caches the
config in memory, so a file edit is ignored until a restart and is then clobbered the next
time the dashboard is edited in the UI. Going through the websocket updates memory and disk
together, so the change is live on a page reload.

No websocket library exists on HAOS, so this speaks just enough of RFC 6455 by hand:
the HTTP upgrade handshake, masked client text frames, and unmasked server frames.
"""
import base64, json, os, socket, struct, sys


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed early")
        buf += chunk
    return buf


def read_frame(sock):
    b1, b2 = recv_exact(sock, 2)
    opcode = b1 & 0x0F
    masked = b2 & 0x80
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", recv_exact(sock, 8))[0]
    mask = recv_exact(sock, 4) if masked else None
    payload = recv_exact(sock, length) if length else b""
    if mask:
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    return opcode, payload


def send_frame(sock, text):
    data = text.encode()
    mask = os.urandom(4)
    masked = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    n = len(data)
    header = bytes([0x81])                      # FIN + text opcode
    if n < 126:
        header += bytes([0x80 | n])
    elif n < (1 << 16):
        header += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        header += bytes([0x80 | 127]) + struct.pack(">Q", n)
    sock.sendall(header + mask + masked)


def recv_json(sock):
    while True:
        opcode, payload = read_frame(sock)
        if opcode == 0x8:
            raise ConnectionError("server closed connection")
        if opcode == 0x9:                       # ping -> pong
            sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
            continue
        if opcode in (0x1, 0x2):
            return json.loads(payload.decode())


def main():
    host, port, path = "supervisor", 80, "/core/websocket"
    token = os.environ["SUPERVISOR_TOKEN"]
    url_path = sys.argv[1]
    config = json.load(open(sys.argv[2]))

    sock = socket.create_connection((host, port), timeout=30)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    ).encode())

    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(4096)
    if b"101" not in resp.split(b"\r\n")[0]:
        print("handshake failed:", resp.split(b"\r\n")[0].decode()); sys.exit(1)

    hello = recv_json(sock)
    if hello.get("type") != "auth_required":
        print("unexpected greeting:", hello); sys.exit(1)
    send_frame(sock, json.dumps({"type": "auth", "access_token": token}))
    auth = recv_json(sock)
    if auth.get("type") != "auth_ok":
        print("auth failed:", auth); sys.exit(1)
    print("authenticated, ha version:", auth.get("ha_version"))

    send_frame(sock, json.dumps({
        "id": 1, "type": "lovelace/config/save",
        "url_path": url_path, "config": config,
    }))
    while True:
        msg = recv_json(sock)
        if msg.get("id") == 1:
            if msg.get("success"):
                print("SAVED ok")
            else:
                print("SAVE FAILED:", json.dumps(msg.get("error", msg)))
                sys.exit(1)
            break
    sock.close()


if __name__ == "__main__":
    main()
