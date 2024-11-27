CONTAINER_NAME := gpt-mission-planner
REPO_NAME := gpt-mission-planner

repo-init:
	python3.11 -m pip install black pre-commit mypy && \
	pre-commit install

build-image:
	docker build --build-arg UID=$(shell id -u) --build-arg GID=$(shell id -g) . -t ${CONTAINER_NAME} --target local

bash:
	docker run -it --rm \
	--user $(shell id -u):$(shell id -g) \
	-v ./Makefile:/${REPO_NAME}/Makefile:Z \
	-v ./app/:/${REPO_NAME}/app:Z \
	--env-file ~/.gpt/token.env \
	--net=host \
	${CONTAINER_NAME} \
	/bin/bash

run:
	python3 ./app/mission_planner.py

server:
	nc -l 0.0.0.0 12345
