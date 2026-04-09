.PHONY: install lint format test check proto

PROTO_SRC := src/openbad/nervous_system/schemas
PROTO_FILES := $(wildcard $(PROTO_SRC)/*.proto)

install:
	pip install -e ".[dev]"

proto:
	python -m grpc_tools.protoc \
		--proto_path=$(PROTO_SRC) \
		--python_out=$(PROTO_SRC) \
		$(PROTO_FILES)
	@# Fix protoc flat imports → relative package imports
	@cd $(PROTO_SRC) && \
		sed -i 's/^import common_pb2 as common__pb2$$/from . import common_pb2 as common__pb2/' *_pb2.py

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

check: lint
	ruff format --check src/ tests/

test:
	pytest -m "not integration"

test-all:
	pytest
