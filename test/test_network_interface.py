"""
Tests for NetworkInterface with length-prefixed protocol.
"""

import socket
import struct
import json
import threading
import logging
import tempfile
import sys
from pathlib import Path

import pytest

# Add app directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from network_interface import NetworkInterface


class MockServer:
    """Simple mock server to receive and validate messages."""

    def __init__(self, host="127.0.0.1", port=0):
        self.host = host
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, port))
        self.port = self.server_socket.getsockname()[1]
        self.server_socket.listen(1)
        self.received_messages = []
        self.thread = None
        self.running = False

    def _receive_length_prefixed_data(self, client_socket):
        """Receive a single length-prefixed message."""
        # Read 4-byte length prefix
        length_data = client_socket.recv(4)
        if len(length_data) < 4:
            return None

        length = struct.unpack("!I", length_data)[0]

        # Read exact payload
        data = b""
        while len(data) < length:
            chunk = client_socket.recv(min(4096, length - len(data)))
            if not chunk:
                break
            data += chunk

        return data if len(data) == length else None

    def _accept_connection(self):
        """Accept connection and receive all messages."""
        client_socket, _ = self.server_socket.accept()

        # Receive messages until connection closes or no more data
        while self.running:
            data = self._receive_length_prefixed_data(client_socket)
            if data is None:
                break
            self.received_messages.append(data)

        client_socket.close()

    def start(self):
        """Start server in background thread."""
        self.running = True
        self.thread = threading.Thread(target=self._accept_connection)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        """Stop server and close socket."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)
        self.server_socket.close()


@pytest.fixture
def test_server():
    """Fixture providing a test server."""
    server = MockServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def logger():
    """Fixture providing a logger."""
    logging.basicConfig(level=logging.INFO)
    return logging.getLogger("test")


@pytest.fixture
def sample_xml_file():
    """Fixture providing a temporary XML file."""
    xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<Mission>
    <MoveToGPSLocation latitude="37.123456" longitude="-120.654321"/>
</Mission>"""

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
        f.write(xml_content)
        temp_path = f.name

    yield temp_path

    # Cleanup
    Path(temp_path).unlink(missing_ok=True)


@pytest.fixture
def sample_tree_points():
    """Fixture providing sample tree points."""
    return [
        {
            "tree_index": 1,
            "row": 1,
            "col": 1,
            "lat": 37.123456,
            "lon": -120.654321,
            "row_waypoints": [(37.123457, -120.654320)],
        },
        {
            "tree_index": 2,
            "row": 1,
            "col": 2,
            "lat": 37.123458,
            "lon": -120.654319,
            "row_waypoints": [(37.123457, -120.654320)],
        },
    ]


def test_send_xml_only(test_server, logger, sample_xml_file):
    """Test sending only XML file without tree points."""
    # Create network interface
    nic = NetworkInterface(logger, test_server.host, test_server.port)
    nic.init_socket()

    # Send only XML (no tree points)
    nic.send_file(sample_xml_file, tree_points=None)
    nic.close_socket()

    # Give server time to receive
    import time

    time.sleep(0.1)

    # Verify only one message received (XML)
    assert len(test_server.received_messages) == 1

    # Verify XML content
    xml_data = test_server.received_messages[0]
    assert b'<?xml version="1.0"' in xml_data
    assert b"<Mission>" in xml_data
    assert b"MoveToGPSLocation" in xml_data


def test_send_xml_and_tree_points(
    test_server, logger, sample_xml_file, sample_tree_points
):
    """Test sending both XML file and tree points."""
    # Create network interface
    nic = NetworkInterface(logger, test_server.host, test_server.port)
    nic.init_socket()

    # Send XML and tree points
    nic.send_file(sample_xml_file, tree_points=sample_tree_points)
    nic.close_socket()

    # Give server time to receive
    import time

    time.sleep(0.1)

    # Verify two messages received (XML + JSON)
    assert len(test_server.received_messages) == 2

    # Verify XML content
    xml_data = test_server.received_messages[0]
    assert b'<?xml version="1.0"' in xml_data
    assert b"<Mission>" in xml_data

    # Verify JSON content
    json_data = test_server.received_messages[1]
    tree_points = json.loads(json_data.decode("utf-8"))

    assert len(tree_points) == 2
    assert tree_points[0]["tree_index"] == 1
    assert tree_points[0]["row"] == 1
    assert tree_points[0]["col"] == 1
    assert tree_points[0]["lat"] == 37.123456
    assert tree_points[0]["lon"] == -120.654321
    assert tree_points[1]["tree_index"] == 2


def test_length_prefix_correctness(test_server, logger, sample_xml_file):
    """Test that length prefixes are correct."""
    # Create network interface
    nic = NetworkInterface(logger, test_server.host, test_server.port)
    nic.init_socket()

    # Read original XML to know expected length
    with open(sample_xml_file, "rb") as f:
        expected_xml = f.read()

    # Send XML
    nic.send_file(sample_xml_file, tree_points=None)
    nic.close_socket()

    # Give server time to receive
    import time

    time.sleep(0.1)

    # Verify received data matches original
    assert len(test_server.received_messages) == 1
    assert test_server.received_messages[0] == expected_xml


def test_empty_tree_points_list(test_server, logger, sample_xml_file):
    """Test sending empty tree points list (should send only XML)."""
    # Create network interface
    nic = NetworkInterface(logger, test_server.host, test_server.port)
    nic.init_socket()

    # Send with empty list (falsy value, should be treated as None)
    nic.send_file(sample_xml_file, tree_points=[])
    nic.close_socket()

    # Give server time to receive
    import time

    time.sleep(0.1)

    # Empty list is falsy but not None, so it will send empty JSON array
    # This tests the current behavior - you may want to change this
    assert len(test_server.received_messages) == 2
    json_data = test_server.received_messages[1]
    tree_points = json.loads(json_data.decode("utf-8"))
    assert tree_points == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
