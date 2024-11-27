import socket
import logging


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

    def send_file(self, file_path) -> None:
        bytes_sent: int = 0

        with open(file_path, "rb") as file:
            chunk: bytes = file.read(1024)
            while chunk:
                bytes_sent += self.client_socket.send(chunk)
                chunk = file.read(1024)

        self.logger.info("File sent successfully.")

    def close_socket(self) -> None:
        self.client_socket.close()
