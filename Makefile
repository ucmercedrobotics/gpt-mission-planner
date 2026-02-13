IMAGE := ghcr.io/ucmercedrobotics/gpt-mission-planner
WORKSPACE := gpt-mission-planner
CONFIG := ./app/config/localhost.yaml
WEB_PORT ?= 8002
MISSION_PORT ?= 12346
TARGET ?= prod

# set PLATFORM to linux/arm64 on silicon mac, otherwise linux/amd64
ARCH := $(shell uname -m)
PLATFORM := linux/amd64
ENABLE_VERIFICATION ?= false
BUILD_SPOT ?= true
ifneq (,$(filter $(ARCH),arm64 aarch64))
	PLATFORM := linux/arm64
	ENABLE_VERIFICATION := false
	CONFIG := ./app/config/localhost_mac.yaml
endif

repo-init:
	python3 -m pip install pre-commit==3.4.0 && \
	pre-commit install && \
	git submodule update --init --recursive

# TODO: enable spin compilation via source to run on ARM
push:
	docker buildx build --push \
	--platform linux/arm64,linux/amd64 \
	--build-arg ENABLE_VERIFICATION=false \
	. -t ${IMAGE} --target ${TARGET}

build-image:
	docker build \
		--platform=$(PLATFORM) \
		--build-arg ENABLE_VERIFICATION=$(ENABLE_VERIFICATION) \
		--build-arg BUILD_SPOT=$(BUILD_SPOT) \
		. -t ${IMAGE} --target ${TARGET}

bash:
	docker run -it --rm \
		--platform=$(PLATFORM) \
		-v ./Makefile:/${WORKSPACE}/Makefile:Z \
		-v ./app/:/${WORKSPACE}/app:Z \
		-v ./schemas/:/${WORKSPACE}/schemas:Z \
		-v ./scripts/:/${WORKSPACE}/scripts:Z \
		-v ./logs:/${WORKSPACE}/logs:Z \
		--env-file .env \
		-p ${WEB_PORT}:${WEB_PORT} \
		-p ${MISSION_PORT}:${MISSION_PORT}/udp \
		${IMAGE} \
		/bin/bash

shell:
	CONTAINER_PS=$(shell docker ps -aq --filter ancestor=${IMAGE}) && \
	docker exec -it $${CONTAINER_PS} bash

run:
	python3 ./app/cli.py --config ${CONFIG}

webapp:
	uvicorn app.server:app --host 0.0.0.0 --port 8002

server:
	nc -lk 172.17.0.1 12346 > test.bin

prod:
	docker run --rm \
		--platform=$(PLATFORM) \
		-v ./logs:/gpt-mission-planner/logs:Z \
		--env-file .env \
		-p ${WEB_PORT}:${WEB_PORT} \
		-p ${MISSION_PORT}:${MISSION_PORT}/udp \
		${IMAGE}:latest
