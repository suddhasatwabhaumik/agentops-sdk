# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

# Virtual environment detection (uses active VIRTUAL_ENV or ./venv or ./.venv)
VENV ?= $(if $(VIRTUAL_ENV),$(VIRTUAL_ENV),$(if $(wildcard ./venv/bin/python),$(patsubst %/bin/python,%,$(wildcard ./venv/bin/python)),$(if $(wildcard ./.venv/bin/python),$(patsubst %/bin/python,%,$(wildcard ./.venv/bin/python)),)))

ifeq ($(VENV),)
PYTHON = python3
PIP = python3 -m pip
INSTALL_PIP = ./venv/bin/python -m pip
RUFF = ruff
MYPY = mypy
PYTEST = pytest
else
PYTHON = $(VENV)/bin/python
PIP = $(PYTHON) -m pip
INSTALL_PIP = $(PIP)
RUFF = $(VENV)/bin/ruff
MYPY = $(VENV)/bin/mypy
PYTEST = $(VENV)/bin/pytest
endif

GCP_REGION ?= europe-west4
GCP_PROJECT ?= $(or $(GOOGLE_CLOUD_PROJECT),$(filter-out (unset),$(shell gcloud config get-value project 2>/dev/null)))
AR_REPOSITORY ?= agentops-python-repo

.DEFAULT_GOAL := help

.PHONY: help set-project set-env venv install format lint typecheck test build-sdk publish-sdk clean

help:
	@echo "========================================================================"
	@echo "                          AgentOps SDK & Tools                          "
	@echo "========================================================================"
	@echo "Usage: make <target>"
	@echo ""
	@echo "Available Targets:"
	@echo "  help         - Show this help menu with all available options"
	@echo "  set-project  - Configure gcloud project & ADC quota for $(GCP_PROJECT)"
	@echo "  install      - Install SDK in editable mode with [dev,all] & MCP deps"
	@echo "  format       - Auto-format codebase using ruff format"
	@echo "  lint         - Run ruff check and ruff format --check"
	@echo "  typecheck    - Run strict mypy type checks on SDK and MCP server"
	@echo "  test         - Run unit and contract pytest suites"
	@echo "  venv         - Create local Python virtual environment (./venv)"
	@echo "  build-sdk    - Clean and build Python wheel/sdist packages for SDK"
	@echo "  publish-sdk  - Build and publish SDK package to Google Artifact Registry"
	@echo "  clean        - Remove build artifacts, caches, and temporary files"
	@echo ""
	@echo "Configuration Variables (Override with VAR=value):"
	@echo "  GCP_PROJECT   (default: $(GCP_PROJECT))"
	@echo "  GCP_REGION    (default: $(GCP_REGION))"
	@echo "  AR_REPOSITORY (default: $(AR_REPOSITORY))"
	@echo "========================================================================"

set-project:
	@if [ -n "$(GCP_PROJECT)" ] && [ "$(GCP_PROJECT)" != "(unset)" ]; then \
		gcloud config set project "$(GCP_PROJECT)" && \
		(gcloud auth application-default set-quota-project "$(GCP_PROJECT)" || true); \
	else \
		echo "GCP_PROJECT is not set; pass GCP_PROJECT=<project-id> or run 'gcloud config set project <id>'."; \
		exit 1; \
	fi

set-env: set-project

venv:
	@if [ ! -x "venv/bin/python" ] && [ ! -x ".venv/bin/python" ]; then \
		echo "Creating virtual environment at ./venv..."; \
		python3 -m venv venv && ./venv/bin/python -m pip install --upgrade pip; \
	fi

install:
	@if [ -z "$(VIRTUAL_ENV)" ] && [ ! -x "./venv/bin/python" ] && [ ! -x "./.venv/bin/python" ]; then \
		$(MAKE) venv; \
	fi
	$(INSTALL_PIP) install -e ".[dev,all]"
	@if [ -f "./mcp_servers/requirements.txt" ]; then \
		$(INSTALL_PIP) install -r ./mcp_servers/requirements.txt; \
	fi

format:
	$(RUFF) format sdk/src/ mcp_servers/src/ tests/ tools/ examples/

lint:
	$(RUFF) check sdk/src/ mcp_servers/src/ tests/ tools/ examples/
	$(RUFF) format --check sdk/src/ mcp_servers/src/ tests/ tools/ examples/

typecheck:
	$(MYPY) --strict sdk/src/agentops_sdk mcp_servers/src tools

test:
	$(PYTEST) -v tests/unit tests/contract --eval-mode=in_memory

build-sdk: clean
	$(PIP) install --upgrade build
	$(PYTHON) -m build .

publish-sdk: build-sdk
	@if [ -z "$(GCP_PROJECT)" ] || [ "$(GCP_PROJECT)" = "(unset)" ]; then \
		echo "GCP_PROJECT is not set; pass GCP_PROJECT=<project-id> or run 'gcloud config set project <id>'."; \
		exit 1; \
	fi
	$(PIP) install --upgrade twine keyrings.google-artifactregistry-auth
	$(PYTHON) -m twine upload --repository-url "https://$(GCP_REGION)-python.pkg.dev/$(GCP_PROJECT)/$(AR_REPOSITORY)/" dist/*

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	rm -rf build dist sdk/build sdk/dist .coverage
