CONTAINER_NAME := gpt-mission-planner
REPO_NAME := gpt-mission-planner
CONFIG := ./app/config/localhost.yaml

repo-init:
	python3 -m pip install pre-commit==3.4.0 && \
	pre-commit install

build-image:
	docker build . -t ${CONTAINER_NAME} --target local

bash:
	docker run -it --rm \
	-v ./Makefile:/${REPO_NAME}/Makefile:Z \
	-v ./app/:/${REPO_NAME}/app:Z \
	--env-file ~/.gpt/token.env \
	--net=host \
	${CONTAINER_NAME} \
	/bin/bash

run:
	python3 ./app/mission_planner.py --config ${CONFIG}

server:
	nc -l 0.0.0.0 12345
