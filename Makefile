.DEFAULT_GOAL := test
.PHONY: test pilot freeze

PYTHON ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,$(if $(wildcard .venv/bin/python),.venv/bin/python,python))
TEST_ARGS ?=
CONFIG ?= configs/benchmark.yaml

test:
	"$(PYTHON)" -m pytest $(TEST_ARGS)

# The general four-level benchmark is not implemented; run one sandbox pilot.
pilot:
	$(if $(strip $(TASK)),,$(error Set TASK, for example make pilot TASK=psf__requests-6027))
	"$(PYTHON)" -m raac.cli sandbox validate --stage pilot --task-id "$(TASK)" --config "$(CONFIG)"

freeze:
	"$(PYTHON)" scripts/freeze_config.py $(if $(FREEZE_OUTPUT),--output "$(FREEZE_OUTPUT)")
