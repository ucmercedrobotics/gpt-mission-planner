# Contributing to GPT Mission Planner

Thank you for your interest in the planner. This file tells you how to get help, how to report a problem, and how to send a change.

## Get help

- Open a [GitHub issue](https://github.com/ucmercedrobotics/gpt-mission-planner/issues) and start the title with `Question:`.
- For questions that you cannot ask in public, send an email to the maintainer: Marcos Abel Zuzuárregui, <mzuzuarregui@ucmerced.edu>.

## Report a bug

Open a [GitHub issue](https://github.com/ucmercedrobotics/gpt-mission-planner/issues). Give this information:

1. The planner version: the release tag (for example, `v1.0.0`) or the commit hash.
2. How you ran the planner: the web interface (`make prod`) or the command-line interface (`make run`), and your host (operating system and architecture).
3. The configuration file that you used (for example, `app/config/kinova.yaml`), and any changes that you made to it.
4. The LLM model, and the routing mode (`online`, `offline` or `auto`).
5. The request text that you gave the planner.
6. The generated mission and the error output. The planner writes them to `logs/`.

**Do not put API keys or the contents of your `.env` file in an issue.**

## Request a feature or a new robot

- For a new feature, open an issue that describes the use case first. Then we can agree on the approach before you write code.
- The action-primitive schemas (XSD) for each robot are in a separate repository, [gpt-mission-planner-schemas](https://github.com/ucmercedrobotics/gpt-mission-planner-schemas), which this repository includes as the `schemas/` submodule. Send a new or changed robot schema to that repository.

## Set up a development environment

You need Docker, Git and Python 3 on the host.

```bash
git clone --recurse-submodules https://github.com/ucmercedrobotics/gpt-mission-planner.git
cd gpt-mission-planner
make repo-init          # installs the pre-commit hooks and updates the submodules
```

Make a `.env` file with your API keys, as the [README](README.md) describes. Then build the image and open a shell in the container:

```bash
make build-image        # add ENABLE_VERIFICATION=true to build SPIN and Spot for formal verification
make bash
```

The container mounts `app/`, `schemas/`, `scripts/` and `test/` from your clone, so you can edit files on the host and run them in the container.

## Run the tests

In the container:

```bash
python3 -m pytest test/ -v
```

The tests do not call an LLM, so they do not need API keys. But `make bash` reads `.env`, so the file must exist (it can be empty). Add tests for each new behavior. A pull request must pass the tests in CI.

## Code style

The pre-commit hooks run [black](https://github.com/psf/black) and remove trailing whitespace. `make repo-init` installs them. To run them on all files:

```bash
pre-commit run --all-files
```

## Send a pull request

1. Fork the repository, and make a branch from `main`.
2. Keep each pull request to one change. Write a description that says what changes and why.
3. Add or update the tests.
4. Update the documentation when the behavior changes:
   - `README.md` for installation and usage,
   - `app/config/README.md` for configuration options,
   - `docs/robot_contract.md` and `scripts/mock_robot.py` together for changes to the robot-side contract (registration, heartbeats, TCP format or acknowledgement).
5. Make sure that the pre-commit hooks and the tests pass.

A maintainer reviews each pull request. The maintainers review issues and pull requests as their time permits.

## Maintainers

- Marcos Abel Zuzuárregui (lead developer)
- Stefano Carpin (principal investigator)

UC Merced Robotics Lab, University of California, Merced.

## License

This project uses the [Apache License 2.0](LICENSE). When you send a contribution, you agree that it is licensed under the same terms.
