# GPT-Powered Robot Mission Planner
[![github](https://img.shields.io/badge/GitHub-ucmercedrobotics-181717.svg?style=flat&logo=github)](https://github.com/ucmercedrobotics)
[![website](https://img.shields.io/badge/Website-UCMRobotics-5087B2.svg?style=flat&logo=telegram)](https://robotics.ucmerced.edu/)
[![python](https://img.shields.io/badge/Python-3.11-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org)
[![pre-commits](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
<!-- TODO: work to enable pydocstyle -->
<!-- [![pydocstyle](https://img.shields.io/badge/pydocstyle-enabled-AD4CD3)](http://www.pydocstyle.org/en/stable/) -->

<!-- [![arXiv](https://img.shields.io/badge/arXiv-2409.04653-b31b1b.svg)](https://arxiv.org/abs/2409.04653) -->

Make sure you initialize the repo with pre-commit hooks:
```bash
make repo-init
```

## How To Run GPT Mission Planner
### GPT Token
Create a file at `~/.gpt/token.env` and add your token in environment variable structure:
```bash
OPENAI_API_TOKEN=<my_token_here>
```

### Docker
NOTE: if running on Mac, ensure you have network mode enabled for this to work. This capability is only available on version 4.29+ of Docker Desktop.
If working in Linux, will work on any version.

```bash
$ make build-image
```

```bash
$ make bash
```

The above two commands will start the build and bash process of the Docker environment to execute your GPT Mission Planner.
From within the Docker container, execute `make run` to request your first mission plan.

### Current Process
This can connect to AgBot over TCP, but running it standalone to debug mission plans can be done as follows:
```bash
$ make server
nc -l 0.0.0.0 12345
...
```
Make sure to run this **first** before running the mission planner (`make run`).

### Example Execution:
On the host machine running a listening server. Make sure the IP/port matches the YAML config file IP/port:
```bash
$ make server
nc -l 0.0.0.0 12345
...
```

```bash
$ make build-image
docker build . -t gpt-mission-planner --target local
...
...

$ make bash
docker run -it --rm \
        -v ./Makefile:/gpt-mission-planner/Makefile:Z \
        -v ./app/:/gpt-mission-planner/app:Z \
        --env-file ~/.gpt/token.env \
        --net=host \
        gpt-mission-planner \
        /bin/bash
root@linuxkit-965cbccc7c1e:/gpt-mission-planner#

root@linuxkit-965cbccc7c1e:/gpt-mission-planner# make run
python3 ./app/mission_planner.py
Enter the specifications for your mission plan: Take a thermal picture of every other tree on the farm.
File sent successfully.
```
