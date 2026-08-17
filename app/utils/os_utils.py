from typing import Tuple
from pathlib import Path
import tempfile
import subprocess
import os
import stat
import struct

from jinja2 import Environment, FileSystemLoader


def execute_shell_cmd(command: list) -> Tuple[int, str]:
    ret: int = 0
    out: str = ""

    try:
        out = str(subprocess.check_output(command))
    except subprocess.CalledProcessError as err:
        ret = err.returncode
        out = str(err.output)

    return ret, out


def write_out_file(dir: str, mp_out: str | None) -> str:
    assert isinstance(mp_out, str)

    # Create a temporary file in the specified directory
    with tempfile.NamedTemporaryFile(dir=dir, delete=False, mode="w") as temp_file:
        temp_file.write(mp_out)
        # name of temp file output
        temp_file_name = temp_file.name
        temp_file.close()

    os.chmod(temp_file_name, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

    return temp_file_name


def write_out_binary_file(dir: str, chunks: list[bytes]) -> str:
    """Write length-prefixed chunks to a temp .bin file in the given directory.

    Mirrors the wire format `NetworkInterface._send_length_prefixed_data` puts
    on the socket (a 4-byte big-endian length before each chunk), so the
    result is the same payload a robot would receive -- e.g. the XML mission
    followed by the tree-points JSON. Tools like `bin_to_kml.py` already parse
    this format.
    """
    with tempfile.NamedTemporaryFile(
        dir=dir, delete=False, mode="wb", suffix=".bin"
    ) as temp_file:
        for chunk in chunks:
            temp_file.write(struct.pack("!I", len(chunk)) + chunk)
        temp_file_name = temp_file.name

    os.chmod(temp_file_name, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

    return temp_file_name


def read_file(path: str, variables: dict | None = None) -> str:
    if path.endswith(".j2"):
        template_path = Path(path)
        env = Environment(
            loader=FileSystemLoader(template_path.parent),
            trim_blocks=True,
        )
        template = env.get_template(template_path.name)
        return template.render(**(variables or {}))

    with open(path, "r") as f:
        return f.read()
