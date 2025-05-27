CONTAINER_NAME := gpt-mission-planner
REPO_NAME := gpt-mission-planner
CONFIG := ./app/config/localhost.yaml

# set PLATFORM to linux/arm64 on silicon mac, otherwise linux/amd64
ARCH := $(shell uname -m)
PLATFORM := linux/amd64
ifeq ($(ARCH),arm64)
	PLATFORM := linux/arm64
endif

# use prebuilt SPOT on x86 and x64, otherwise build from source
BUILD_SPOT ?= true
ifneq ($(filter $(ARCH),x86_64 i386),)
	BUILD_SPOT := false
endif

repo-init:
	python3 -m pip install pre-commit==3.4.0 && \
	pre-commit install && \
	git submodule update --init --recursive

build-image:
	docker buildx build --load \
		--platform=$(PLATFORM) \
		--build-arg BUILD_SPOT=$(BUILD_SPOT) \
		. -t ${CONTAINER_NAME} --target local

bash:
	docker run -it --rm \
		--platform=$(PLATFORM) \
		-v ./Makefile:/${REPO_NAME}/Makefile:Z \
		-v ./app/:/${REPO_NAME}/app:Z \
		-v ./schemas/:/${REPO_NAME}/schemas:Z \
		--env-file .env \
		--net=host \
		${CONTAINER_NAME} \
		/bin/bash

run:
	python3 ./app/mission_planner.py --config ${CONFIG}

server:
	nc -lk 0.0.0.0 12346
