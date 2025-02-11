FROM python:3.11 AS builder

# install requirements through pip
COPY requirements.txt /requirements.txt
RUN python -m pip install -r /requirements.txt

FROM python:3.11 AS base

# top line is general software dev tools, second is for spin, third is for spot
RUN apt-get -y update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y software-properties-common build-essential wget netcat-openbsd vim \
    byacc flex graphviz
    # see comment below about SPOT
    # bison swig latexmk texlive-latex-extra texlive-fonts-extra fonts-roboto texlive-science pdf2svg groff r-base

# NOTE: sometimes the GPG key goes down from SPOT so we have to build manually. Keep as backup.
# COPY spot-spot-2-12-2.tar /spot-spot-2-12-2.tar
# spot install from source. for some reason apt isn't working with keys
# RUN tar -xf /spot-spot-2-12-2.tar && cd spot-spot-2-12-2 \
#     export MAKEFLAGS='-j8' NBPROC='8' && \
#     # autoreconf -vfi && ./configure --enable-max-accsets=256 --enable-pthread && \
#     make
# ENV LD_LIBRARY_PATH="$LD_LIBRARY_PATH:/usr/local/lib"

# install SPOT w/ python bindings from apt
RUN wget -q -O - https://www.lrde.epita.fr/repo/debian.gpg | apt-key add - && \
    echo 'deb http://www.lrde.epita.fr/repo/debian/ stable/' >> /etc/apt/sources.list && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y spot libspot-dev spot-doc python3-spot

# SPOT package is installed into python3 folder, not python3.11
ENV PYTHONPATH "/usr/lib/python3/dist-packages:$PYTHONPATH"

# install spin -> prebuilt image
RUN wget https://github.com/nimble-code/Spin/archive/refs/tags/version-6.5.2.tar.gz && \
    gunzip *.tar.gz && \
    tar -xf *.tar && \
    cd Spin-version-6.5.2/Bin && \
    gunzip spin651_linux64.gz && \
    ./spin651_linux64 -V && \
    mv ./spin651_linux64 /usr/local/bin/spin

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
