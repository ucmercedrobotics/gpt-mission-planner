import socket
import logging
import struct
import json
from typing import Optional


class NetworkInterface:
    def __init__(self, logger: logging.Logger, host="127.0.0.1", port=12345):
        self.logger: logging.Logger = logger
        # connect to server as client
        self.host: str = host
        self.port: int = port
        self.client_socket: socket.socket = socket.socket(
            socket.AF_INET, socket.SOCK_STREAM
        )
        self.client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def init_socket(self) -> None:
        self.client_socket.connect((self.host, self.port))

    def _send_length_prefixed_data(self, data: bytes) -> None:
        """Send data with 4-byte length prefix."""
        length = struct.pack("!I", len(data))  # 4-byte big-endian unsigned int
        self.client_socket.sendall(length + data)

    def send_xml_file(self, file_path: str) -> None:
        """Send XML file with length prefix."""
        with open(file_path, "rb") as file:
            xml_data = file.read()

        self._send_length_prefixed_data(xml_data)
        self.logger.debug(f"XML file sent successfully ({len(xml_data)} bytes).")

    def send_tree_points(self, tree_points: list) -> None:
        """Send tree points dictionary as JSON with length prefix."""
        json_data = json.dumps(tree_points).encode("utf-8")
        self._send_length_prefixed_data(json_data)
        self.logger.debug(
            f"Tree points sent successfully ({len(tree_points)} trees, {len(json_data)} bytes)."
        )

    def send_file(self, file_path: str, tree_points: Optional[list] = None) -> None:
        """Send XML file and optionally tree points, both with length prefixes."""
        self.send_xml_file(file_path)
        if tree_points is not None:
            self.send_tree_points(tree_points)

    def close_socket(self) -> None:
        self.client_socket.close()
