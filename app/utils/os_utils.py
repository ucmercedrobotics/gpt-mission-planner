from typing import Tuple
import tempfile
import subprocess
import os
import stat


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
