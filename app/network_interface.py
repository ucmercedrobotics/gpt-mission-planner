import socket
import logging
import struct
import json
from typing import Optional, Any

# Without these a robot that is powered on but wedged blocks the planner
# forever: connect() and sendall() have no timeout by default.
DEFAULT_TIMEOUT_S: float = 5.0
# Read side gets longer -- the robot may be parsing a large tree payload before
# it can answer.
DEFAULT_ACK_TIMEOUT_S: float = 10.0
# A sane ceiling on the ack frame so a confused peer cannot make us allocate.
MAX_ACK_BYTES: int = 64 * 1024


class NetworkInterface:
    def __init__(
        self,
        logger: logging.Logger,
        host="127.0.0.1",
        port=12345,
        timeout: float = DEFAULT_TIMEOUT_S,
        ack_timeout: float = DEFAULT_ACK_TIMEOUT_S,
    ):
        self.logger: logging.Logger = logger
        # connect to server as client
        self.host: str = host
        self.port: int = port
        self.timeout: float = timeout
        self.ack_timeout: float = ack_timeout
        self.client_socket: socket.socket = socket.socket(
            socket.AF_INET, socket.SOCK_STREAM
        )
        self.client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def init_socket(self) -> None:
        # Create a new socket if the previous one was closed
        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.client_socket.settimeout(self.timeout)
        self.client_socket.connect((self.host, self.port))

    def _send_length_prefixed_data(self, data: bytes) -> None:
        """Send data with 4-byte length prefix."""
        length = struct.pack("!I", len(data))  # 4-byte big-endian unsigned int
        self.client_socket.sendall(length + data)

    def _recv_exactly(self, count: int) -> Optional[bytes]:
        """Read exactly `count` bytes, or None if the peer closed first."""
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = self.client_socket.recv(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def send_xml_file(self, file_path: str) -> None:
        """Send XML file with length prefix."""
        with open(file_path, "rb") as file:
            xml_data = file.read()

        self._send_length_prefixed_data(xml_data)
        self.logger.debug(f"XML file sent successfully ({len(xml_data)} bytes).")

    def send_tree_points(self, tree_points: dict[str, Any]) -> None:
        """Send tree-point payload as JSON with length prefix."""
        json_data = json.dumps(tree_points).encode("utf-8")
        self._send_length_prefixed_data(json_data)
        tree_count = len(tree_points.get("trees", []))

        self.logger.debug(
            f"Tree points sent successfully ({tree_count} trees, {len(json_data)} bytes)."
        )

    def send_file(
        self, file_path: str, tree_points: Optional[dict[str, Any]] = None
    ) -> None:
        """Send XML file and optionally tree points, both with length prefixes."""
        self.send_xml_file(file_path)
        if tree_points is not None:
            self.send_tree_points(tree_points)

    def finish_sending(self) -> None:
        """Half-close the write side once every frame is out.

        This is what lets the robot know the payload is complete. The receiver
        has always detected "no tree-points frame" by reading EOF, and it still
        can: shutting down only our write direction gives it that EOF while
        leaving the read direction open so it can still send the ack back. If
        we simply kept the socket open waiting for the ack, a receiver reading
        until EOF would block forever.
        """
        try:
            self.client_socket.shutdown(socket.SHUT_WR)
        except OSError as exc:
            self.logger.debug("Could not half-close socket: %s", exc)

    def recv_ack(self) -> Optional[dict[str, Any]]:
        """Read the robot's length-prefixed JSON acknowledgement.

        Returns None when the robot closes without acking, which is what a
        receiver that predates the ack frame looks like -- treat it as "sent,
        unacknowledged" rather than a failure.
        """
        self.finish_sending()
        try:
            self.client_socket.settimeout(self.ack_timeout)
            header = self._recv_exactly(4)
            if header is None:
                self.logger.debug("Peer closed without sending an ack.")
                return None

            (length,) = struct.unpack("!I", header)
            if length == 0 or length > MAX_ACK_BYTES:
                self.logger.warning("Ignoring implausible ack frame length: %d", length)
                return None

            body = self._recv_exactly(length)
            if body is None:
                self.logger.warning(
                    "Peer closed mid-ack after %d bytes announced.", length
                )
                return None

            ack = json.loads(body.decode("utf-8"))
            if not isinstance(ack, dict):
                self.logger.warning("Ack was not a JSON object: %r", ack)
                return None
            self.logger.debug("Ack received: %s", ack)
            return ack
        except socket.timeout:
            self.logger.warning(
                "No ack from %s:%d within %.1fs.",
                self.host,
                self.port,
                self.ack_timeout,
            )
            return None
        except (OSError, struct.error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.logger.warning(
                "Malformed or failed ack from %s:%d: %s", self.host, self.port, exc
            )
            return None

    def close_socket(self) -> None:
        try:
            self.client_socket.close()
        except OSError as exc:
            self.logger.debug("Error closing socket: %s", exc)
