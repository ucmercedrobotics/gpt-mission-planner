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

Make sure you initialize the repo with pre-commit hooks:
```bash
make repo-init
```

## How To Run GPT Mission Planner
### GPT Token
Create a `.env` file and add your API tokens:
```bash
OPENAI_API_KEY=<my_token_here>
ANTHROPIC_API_KEY=<my_token_here>
```

### Docker

On ARM Macs, SPOT will be built from source. If necessary, you can force building SPOT from source on x86/64 by running `make build-image BUILD_SPOT=true`.

```bash
$ make build-image
```

```bash
$ make bash
```

The above two commands will start the build and bash process of the Docker environment to execute your GPT Mission Planner.

### Example Execution:
```bash
$ make build-image
docker build . -t gpt-mission-planner --target local
...

$ make bash
docker run -it --rm \
        -v ./Makefile:/gpt-mission-planner/Makefile:Z \
        -v ./app/:/gpt-mission-planner/app:Z \
        --env-file .env \
        --net=host \
        gpt-mission-planner \
        /bin/bash
root@linuxkit-965cbccc7c1e:/gpt-mission-planner#
```

```bash
```bash
$ make server
nc -l 0.0.0.0 12346
```
In another shell:
```bash
root@linuxkit-965cbccc7c1e:/gpt-mission-planner# make run
python3 ./app/mission_planner.py
Enter the specifications for your mission plan: Take a thermal picture of every other tree on the farm.
INFO:root:Successful XML mission plan generation...
```

### TCP Message Format
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

### Graphical UI
A web+mobile app implementation of this program is under development at https://github.com/thomasm6m6/mpui/.

## Test
```bash
$ python -m pytest test/ -v
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
