ARG PYTHON_IMAGE=python:3.11-bookworm
ARG BUILD_SPOT=false
ARG SPOT_VERSION=2.13.1

FROM ${PYTHON_IMAGE} AS builder

# install requirements through pip
COPY requirements.txt /requirements.txt
RUN python -m pip install -r /requirements.txt

FROM ${PYTHON_IMAGE} AS base

ARG BUILD_SPOT
ARG SPOT_VERSION

ENV MAKEFLAGS="-j4"

RUN wget https://github.com/nimble-code/Spin/archive/refs/tags/version-6.5.2.tar.gz && \
    gunzip *.tar.gz && \
    tar -xf *.tar && \
    cd Spin-version-6.5.2/Bin && \
    gunzip spin651_linux64.gz && \
    ./spin651_linux64 -V && \
    mv ./spin651_linux64 /usr/local/bin/spin

RUN if test "$BUILD_SPOT" = "true"; then \
        echo "Building SPOT from source..." && \
        curl -O https://www.lrde.epita.fr/dload/spot/spot-${SPOT_VERSION}.tar.gz && \
        tar xzf spot-${SPOT_VERSION}.tar.gz && cd spot-${SPOT_VERSION} && \
        ./configure && make && make install; \
    else \
        curl -o - https://www.lrde.epita.fr/repo/debian.gpg | apt-key add - && \
        echo 'deb http://www.lrde.epita.fr/repo/debian/ stable/' >> /etc/apt/sources.list && \
        apt-get update && \
        apt-get install -y spot libspot-dev python3-spot; \
    fi

ENV LD_LIBRARY_PATH="$LD_LIBRARY_PATH:/usr/local/lib"

ARG BUILD_SPOT
ARG SPOT_VERSION

ENV MAKEFLAGS="-j4"

RUN apt-get update && apt-get install -y spin

RUN if test "$BUILD_SPOT" = "true"; then \
        echo "Building SPOT from source..." && \
        curl -O https://www.lrde.epita.fr/dload/spot/spot-${SPOT_VERSION}.tar.gz && \
        tar xzf spot-${SPOT_VERSION}.tar.gz && cd spot-${SPOT_VERSION} && \
        ./configure && make && make install; \
    else \
        curl -o - https://www.lrde.epita.fr/repo/debian.gpg | apt-key add - && \
        echo 'deb http://www.lrde.epita.fr/repo/debian/ stable/' >> /etc/apt/sources.list && \
        apt-get update && \
        apt-get install -y spot libspot-dev python3-spot; \
    fi

ENV LD_LIBRARY_PATH="$LD_LIBRARY_PATH:/usr/local/lib"

# top line is general software dev tools, second is for spin, third is for spot
RUN apt-get -y update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y software-properties-common build-essential wget netcat-openbsd vim \
    byacc flex graphviz

# SPOT package is installed into python3 folder, not python3.11
ENV PYTHONPATH "/usr/lib/python3/dist-packages:$PYTHONPATH"

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
