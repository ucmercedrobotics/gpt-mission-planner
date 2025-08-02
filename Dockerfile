ARG PYTHON_IMAGE=python:3.11-bookworm
ARG ENABLE_VERIFICATION=true
ARG BUILD_SPOT=false
ARG SPOT_VERSION=2.13.1
ARG SPIN_VERSION=6.5.2
ARG SPIN_FILE=spin651_linux64

FROM ${PYTHON_IMAGE} AS builder

# install requirements through pip
COPY requirements.txt /requirements.txt
RUN python -m pip install -r /requirements.txt

FROM ${PYTHON_IMAGE} AS base

ARG ENABLE_VERIFICATION
ARG BUILD_SPOT
ARG SPOT_VERSION
ARG SPIN_VERSION
ARG SPIN_FILE

ENV MAKEFLAGS=-j4

RUN apt update && apt install -y byacc flex graphviz

RUN set -e; if test "$ENABLE_VERIFICATION" = true; then \
  curl -Lo- https://github.com/nimble-code/Spin/archive/refs/tags/version-${SPIN_VERSION}.tar.gz | \
  tar -xOzf- Spin-version-${SPIN_VERSION}/Bin/${SPIN_FILE}.gz | gunzip >/usr/local/bin/spin; \
  chmod 0755 /usr/local/bin/spin; spin -V; \
  if test "$BUILD_SPOT" = true; then \
    echo "Building SPOT from source..."; \
    curl -Lo- https://www.lrde.epita.fr/dload/spot/spot-${SPOT_VERSION}.tar.gz | \
    tar -xzf-; cd spot-${SPOT_VERSION}; ./configure; make; make install; \
  else \
    curl -o- https://www.lrde.epita.fr/repo/debian.gpg | apt-key add -; \
    echo 'deb https://www.lrde.epita.fr/repo/debian/ stable/' >> /etc/apt/sources.list && \
    apt update; apt install -y spot libspot-dev python3-spot; \
  fi; \
fi

RUN apt -y update && DEBIAN_FRONTEND=noninteractive apt install -y \
  software-properties-common build-essential wget netcat-openbsd vim

# SPOT package is installed into python3 folder, not python3.11
ENV PYTHONPATH="/usr/lib/python3/dist-packages"

# For more information, please refer to https://aka.ms/vscode-docker-python
# This is particularly for debugging using VSCode
FROM builder AS dev

WORKDIR /gpt-mission-planner
COPY . /gpt-mission-planner

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

# Creates a non-root user with an explicit UID and adds permission to access the /app folder
# For more info, please refer to https://aka.ms/vscode-docker-python-configure-containers
RUN adduser -u 5678 --disabled-password --gecos "" appuser && chown -R appuser /gpt-mission-planner
USER appuser

# During debugging, this entry point will be overridden. For more information, please refer to https://aka.ms/vscode-docker-python-debug
# CMD ["python", "orienteering/orienteering.py"]

# image for running with a GPU: LINUX ONLY
FROM base AS local

# copy over all python files from builder stage and add location to path
COPY --from=builder /usr/local /usr/local

WORKDIR /gpt-mission-planner
