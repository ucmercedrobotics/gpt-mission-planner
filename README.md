# GPT-Powered Robot Mission Planner
[![github](https://img.shields.io/badge/GitHub-ucmercedrobotics-181717.svg?style=flat&logo=github)](https://github.com/ucmercedrobotics)
[![website](https://img.shields.io/badge/Website-UCMRobotics-5087B2.svg?style=flat&logo=telegram)](https://robotics.ucmerced.edu/)
[![python](https://img.shields.io/badge/Python-3.11-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org)
[![pre-commits](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
<!-- [![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/) -->
<!-- TODO: work to enable pydocstyle -->
<!-- [![pydocstyle](https://img.shields.io/badge/pydocstyle-enabled-AD4CD3)](http://www.pydocstyle.org/en/stable/) -->

<!-- [![arXiv](https://img.shields.io/badge/arXiv-2409.04653-b31b1b.svg)](https://arxiv.org/abs/2409.04653) -->

## How To Run GPT Mission Planner
https://github.com/user-attachments/assets/cd18a3b1-1cd3-48e9-ae74-825cca88b508

### ENV Variables
Create a `.env` file and add your API tokens:
```bash
OPENAI_API_KEY=<my_token_here>
ANTHROPIC_API_KEY=<my_token_here>
```
By default, XML planning uses OpenAI and is the only key required.
If using formal verification, add an Anthropic key.

You may also configure the speech-to-text models if you wish.
Below are what you should place as default defaults:
```bash
STT_PROVIDER=local
WHISPER_MODEL=small
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
```

Save this `.env` file in your workspace for the next series of commands.

### Web UI (Local)
Run the web interface in Docker (default http://localhost:8002):

```bash
make prod
```

If you want a different port:

```bash
WEB_PORT=8080 make prod
```

To test mission delivery locally, open another terminal and listen on the mission port (default 12346):

```bash
make server
```
The above will create you a binary file called `test.bin` that you can inspect for the relevant XML mission with JSON orchard map (if necessary).
Ideally, you should connect the webapp to a robot through the GUI.
The above command is simply to debug your output and confirm it's coming through properly.
The options to do so are provided in the settings.

### Developers

If you wish to use the text-based planner without the web UI:
```bash
make build-image
make bash
```

From within the container you can run the text-based planner:
```bash
make run
```
or selecting your own config:
```bash
CONFIG=<path_to_config>.yaml make run
```

Or you can even just run the webapp manually:
```bash
make serve
```

## TCP Message Format
The output of this planner is as follows:

| Order | Field | Type/Size | Description |
| --- | --- | --- | --- |
| 1 | `xml_length` | 4 bytes (uint32, big-endian) | Length of the XML payload in bytes |
| 2 | `xml_payload` | `xml_length` bytes | Raw XML file bytes |
| 3 (optional) | `json_length` | 4 bytes (uint32, big-endian) | Length of the JSON payload in bytes (tree points) |
| 4 (optional) | `json_payload` | `json_length` bytes | UTF-8 JSON array of tree-point dictionaries |

Notes:
- If no tree points are sent, only fields 1–2 are transmitted and the socket closes.
- The receiver detects absence of JSON by `recv` returning empty when attempting the next 4-byte length.
- JSON is UTF-8; XML is sent as raw bytes.

## Test
```bash
python -m pytest test/ -v
...
test/test_network_interface.py::test_send_xml_only PASSED [25%]
test/test_network_interface.py::test_send_xml_and_tree_points PASSED [50%]
test/test_network_interface.py::test_length_prefix_correctness PASSED [75%]
test/test_network_interface.py::test_empty_tree_points_list PASSED [100%]
...
```

## Example Queries
The following queries are used to demonstrate the capabilities of this system:

![Explicit queries](docs/images/explicit.png)

![Implict queries](docs/images/implicit.png)

![Farmer queries](docs/images/farmer.png)

## Citation
If you use this work, please cite:

```latex
@inproceedings{zuzuarregui_carpin_2025,
	author    = {M. A. Zuzu\'{a}rregui and S. Carpin},
	title     = {Leveraging LLMs for Mission Planning in Precision Agriculture},
	booktitle = {Proceedings of the IEEE International Conference on Robotics and Automation},
	pages     = {7146--7152},
	year      = {2025}
}
```
