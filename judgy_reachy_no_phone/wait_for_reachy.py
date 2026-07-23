"""Wait for Reachy's WebRTC producer."""

import json
import sys
import time

from websockets.sync.client import connect


def main() -> int:
    host = sys.argv[1]
    deadline = time.monotonic() + 45
    last_error = "producer not advertised"
    healthy_checks = 0

    while time.monotonic() < deadline:
        try:
            with connect(f"ws://{host}:8443", open_timeout=2) as websocket:
                websocket.recv(timeout=2)
                websocket.send(json.dumps({"type": "list"}))
                response = json.loads(websocket.recv(timeout=2))
                if any(
                    item.get("meta", {}).get("name") == "reachymini"
                    for item in response.get("producers", [])
                ):
                    healthy_checks += 1
                    if healthy_checks == 3:
                        print(f"Reachy WebRTC ready at {host}:8443")
                        return 0
                else:
                    healthy_checks = 0
        except Exception as exc:
            last_error = str(exc)
            healthy_checks = 0
        time.sleep(1)

    print(f"Reachy WebRTC not ready at {host}:8443: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
