#!/usr/bin/env python3
"""Reference robot for the mission planner's fleet contract.

This is the executable version of docs/robot_contract.md. It does exactly what
a real robot must do and nothing else:

  1. POST /robots/register to the planner's discovery port
  2. POST /robots/{id}/heartbeat every few seconds
  3. listen on a TCP port, decode the length-prefixed mission frames
  4. write back a length-prefixed JSON ack
  5. DELETE /robots/{id} on shutdown

Standard library only, so it runs on a robot without installing anything.

    python3 scripts/mock_robot.py --robot-id amiga-01 --schema amiga_btcpp \
        --bt-port 12346 --planner http://localhost:8003

Run two of them on different --bt-port values to exercise fleet planning:

    python3 scripts/mock_robot.py --robot-id amiga-01 --bt-port 12346 &
    python3 scripts/mock_robot.py --robot-id amiga-02 --bt-port 12347 &

If a real BT executor is already listening on the port, pass --no-listen and
this becomes a discovery client only -- registration and heartbeats, with
missions going to your executor:

    python3 scripts/mock_robot.py --robot-id amiga-01 --bt-port 12346 --no-listen

Note the direction of the mission channel: the planner is the TCP *client* and
connects out to this port. The robot is always the listener.
"""

import argparse
import json
import logging
import signal
import socket
import socketserver
import struct
import sys
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger("mock-robot")

# Mutated by the heartbeat thread and the mission handler.
STATE = {
    "status": "idle",
    "battery_pct": 0.87,
    "current_mission_id": None,
}
STATE_LOCK = threading.Lock()
SHUTDOWN = threading.Event()


# --- HTTP side: registration and heartbeat --------------------------------


def post_json(url: str, payload: dict, token: str | None = None, timeout: float = 5.0):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("X-Fleet-Token", token)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def delete(url: str, token: str | None = None, timeout: float = 5.0):
    request = urllib.request.Request(url, method="DELETE")
    if token:
        request.add_header("X-Fleet-Token", token)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def register(args) -> dict:
    payload = {
        "robot_id": args.robot_id,
        "name": args.name or args.robot_id,
        "schema": args.schema,
        "bt_endpoint": {
            # None: let the planner use the source IP of this request. A robot
            # rarely knows which of its interfaces the planner can reach.
            "host": args.advertise_host,
            "port": args.bt_port,
            "protocol": "tcp-lenprefix-v1",
        },
        "capabilities": {"actions": args.actions or []},
        "status": STATE["status"],
        "battery_pct": STATE["battery_pct"],
        "heartbeat_interval_s": args.heartbeat_interval,
    }
    if args.position:
        payload["position"] = {"lat": args.position[0], "lon": args.position[1]}

    response = post_json(f"{args.planner}/robots/register", payload, args.token)
    logger.info("Registered: %s", response)
    if not response.get("schema_known", True):
        logger.warning(
            "The planner does not have a schema called '%s'. This robot will be "
            "listed but never given work.",
            args.schema,
        )
    return response


def heartbeat_loop(args) -> None:
    url = f"{args.planner}/robots/{args.robot_id}/heartbeat"
    while not SHUTDOWN.is_set():
        with STATE_LOCK:
            payload = {
                "status": STATE["status"],
                "battery_pct": STATE["battery_pct"],
                "current_mission_id": STATE["current_mission_id"],
            }
        try:
            post_json(url, payload, args.token)
            logger.debug("Heartbeat: %s", payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # The planner restarted and lost the roster. Re-register and
                # carry on -- this is the expected recovery path.
                logger.warning("Planner does not know us (404); re-registering.")
                try:
                    register(args)
                except Exception as retry_exc:
                    logger.error("Re-registration failed: %s", retry_exc)
            else:
                logger.error("Heartbeat rejected: %s", exc)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            logger.warning("Heartbeat failed (planner unreachable?): %s", exc)
        SHUTDOWN.wait(args.heartbeat_interval)


# --- TCP side: receiving a mission ----------------------------------------


def recv_exactly(sock: socket.socket, count: int) -> bytes | None:
    chunks = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket) -> bytes | None:
    """One length-prefixed frame, or None at end of payload.

    The planner half-closes its write side after the last frame, so this
    returns None exactly when there is nothing more coming -- while the socket
    is still writable for the ack.
    """
    header = recv_exactly(sock, 4)
    if header is None:
        return None
    (length,) = struct.unpack("!I", header)
    return recv_exactly(sock, length)


def send_frame(sock: socket.socket, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    sock.sendall(struct.pack("!I", len(body)) + body)


class MissionHandler(socketserver.BaseRequestHandler):
    args = None  # set in main()

    def handle(self) -> None:
        sock: socket.socket = self.request
        sock.settimeout(30.0)
        peer = self.client_address
        logger.info("Mission connection from %s:%s", peer[0], peer[1])

        mission_xml = None
        tree_points = None
        try:
            raw_xml = read_frame(sock)
            if raw_xml is None:
                logger.warning("Peer closed before sending anything.")
                return
            mission_xml = raw_xml.decode("utf-8")
            logger.info("Frame 1: behavior tree, %d bytes", len(raw_xml))

            raw_json = read_frame(sock)
            if raw_json is not None:
                tree_points = json.loads(raw_json.decode("utf-8"))
                logger.info(
                    "Frame 2: %d bytes, %d trees",
                    len(raw_json),
                    len(tree_points.get("trees", [])),
                )
        except (OSError, ValueError, struct.error) as exc:
            logger.error("Failed reading mission: %s", exc)
            return

        mission = (tree_points or {}).get("mission") or {}
        mission_id = mission.get("mission_id")

        logger.info("--- mission %s ---", mission_id or "(no id)")
        if mission:
            logger.info("  for robot:  %s", mission.get("robot_id"))
            logger.info("  aisles:     %s", mission.get("assigned_aisles"))
            trees = mission.get("assigned_tree_indices") or []
            logger.info(
                "  trees:      %d assigned%s",
                len(trees),
                f" ({trees[0]}..{trees[-1]})" if trees else "",
            )
            for other in mission.get("fleet", []):
                logger.info(
                    "  also out:   %s on aisles %s",
                    other.get("robot_id"),
                    other.get("assigned_aisles"),
                )
        else:
            logger.info("  (no mission metadata -- single-robot send)")
        if self.args.print_xml and mission_xml:
            logger.info("  behavior tree:\n%s", mission_xml)

        if self.args.no_ack:
            logger.warning("--no-ack set: closing without acknowledging.")
            return

        accepted = not self.args.reject
        ack = {
            "accepted": accepted,
            "robot_id": self.args.robot_id,
            "mission_id": mission_id,
            "error": self.args.reject or None,
        }
        try:
            send_frame(sock, ack)
            logger.info("Ack sent: %s", ack)
        except OSError as exc:
            logger.error("Could not send ack: %s", exc)
            return

        if accepted:
            threading.Thread(
                target=self._execute, args=(mission_id,), daemon=True
            ).start()

    def _execute(self, mission_id) -> None:
        """Pretend to fly the mission so the planner sees busy -> idle."""
        with STATE_LOCK:
            STATE["status"] = "busy"
            STATE["current_mission_id"] = mission_id
        logger.info(
            "Executing mission %s for %ds...", mission_id, self.args.busy_seconds
        )
        SHUTDOWN.wait(self.args.busy_seconds)
        with STATE_LOCK:
            STATE["status"] = "idle"
            STATE["current_mission_id"] = None
        logger.info("Mission %s complete; back to idle.", mission_id)


class ThreadedTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


# --- entry point ----------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--robot-id", default="mock-01")
    parser.add_argument("--name", default=None)
    parser.add_argument(
        "--schema", default="amiga_btcpp", help="XSD stem, e.g. amiga_btcpp"
    )
    parser.add_argument("--planner", default="http://localhost:8003")
    parser.add_argument("--bt-port", type=int, default=12346)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument(
        "--advertise-host",
        default=None,
        help="Address to advertise. Leave unset so the planner uses our source IP.",
    )
    parser.add_argument(
        "--actions", nargs="*", default=None, help="Supported action names"
    )
    parser.add_argument("--position", nargs=2, type=float, metavar=("LAT", "LON"))
    parser.add_argument("--battery", type=float, default=0.87)
    parser.add_argument("--heartbeat-interval", type=float, default=5.0)
    parser.add_argument("--busy-seconds", type=int, default=10)
    parser.add_argument(
        "--reject",
        metavar="REASON",
        default=None,
        help="Reject missions with this reason",
    )
    parser.add_argument(
        "--no-ack", action="store_true", help="Close without acking (legacy receiver)"
    )
    parser.add_argument("--print-xml", action="store_true")
    parser.add_argument(
        "--no-listen",
        action="store_true",
        help="Do not bind --bt-port. Use when a real BT executor already listens "
        "there and you only want registration and heartbeats.",
    )
    parser.add_argument(
        "--token", default=None, help="X-Fleet-Token, if the planner requires one"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=f"%(asctime)s [{args.robot_id}] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args.planner = args.planner.rstrip("/")
    STATE["battery_pct"] = args.battery

    MissionHandler.args = args
    server = None
    if args.no_listen:
        # Discovery only. The planner will still be told about --bt-port, so it
        # dispatches to whatever is really listening there.
        logger.info(
            "Not binding a receiver (--no-listen). Advertising port %d; something "
            "else must be listening on it.",
            args.bt_port,
        )
    else:
        try:
            server = ThreadedTCPServer((args.bind, args.bt_port), MissionHandler)
        except OSError as exc:
            logger.error("Cannot listen on %s:%d: %s", args.bind, args.bt_port, exc)
            logger.error(
                "Something already holds that port. Either your real BT executor is "
                "running (use --no-listen so this process only registers and "
                "heartbeats), or another mock is up (use a different --bt-port)."
            )
            return 1
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info(
            "Behavior tree receiver listening on %s:%d", args.bind, args.bt_port
        )

    try:
        register(args)
    except Exception as exc:
        logger.error("Registration failed against %s: %s", args.planner, exc)
        logger.error("Is the planner running with fleet.enabled: true?")
        if server is not None:
            server.shutdown()
        return 1

    threading.Thread(target=heartbeat_loop, args=(args,), daemon=True).start()

    def _stop(_signum, _frame):
        SHUTDOWN.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    logger.info("Running. Ctrl-C to deregister and exit.")
    SHUTDOWN.wait()

    logger.info("Shutting down...")
    try:
        delete(f"{args.planner}/robots/{args.robot_id}", args.token)
        logger.info("Deregistered.")
    except Exception as exc:
        # Not fatal: the planner's TTL will drop us within heartbeat_ttl_s.
        logger.warning("Deregistration failed (TTL will handle it): %s", exc)
    if server is not None:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
