#!/bin/bash

# Fail on the first failing command. Without this the script returns the exit
# code of the *last* line only, so a failing `ruff check` would be masked by a
# passing `ty check` and CI would go green on a lint error.
set -e

if [ "$1" == "setup_local" ]; then
  echo "Installing development dependencies..."
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # No global ruff install: `./make.sh format` / `lint` run the version pinned
  # in pyproject.toml, so everyone lints against the same rule set.
  echo "Installing deploy-your-startup-cli..."
  uv tool install --reinstall deploy-your-startup-cli --from .
  echo "Setup complete!"
fi

if [ "$1" == "format" ]; then
  echo "Formatting code and running ruff checks..."
  # `uv run --extra dev` uses the ruff pinned in pyproject.toml. `uvx ruff`
  # would silently fetch the newest release, whose default rule set differs.
  uv run --extra dev ruff format
  uv run --extra dev ruff check --fix
fi

if [ "$1" == "lint" ]; then
  echo "Checking formatting and lint (no changes) — same as CI..."
  uv run --extra dev ruff format --check
  uv run --extra dev ruff check
  uv run --extra dev ty check
fi

if [ "$1" == "test" ]; then
  echo "Running tests..."
  uv run --extra dev pytest
fi

if [ "$1" == "install_tool" ]; then
  echo "Installing deploy-your-startup-cli as a global tool..."
  uv tool install --reinstall deploy-your-startup-cli --from .
fi

if [ "$1" == "dev_install" ]; then
  echo "Installing in development mode..."
  uv pip install -e .
fi

if [ "$1" == "clean" ]; then
  echo "Cleaning build artifacts..."
  rm -rf build/ dist/ *.egg-info
  find . -type d -name __pycache__ -exec rm -rf {} +
  echo "Clean complete!"
fi

if [ "$1" == "help" ] || [ -z "$1" ]; then
  echo "Available commands:"
  echo "  setup_local   - Install uv and deploy-your-startup-cli"
  echo "  format        - Format code and run ruff checks"
  echo "  lint          - Check formatting, lint and types without changing files"
  echo "  test          - Run pytest tests"
  echo "  install_tool  - Install CLI as a global tool"
  echo "  dev_install   - Install in development mode"
  echo "  clean         - Remove build artifacts"
  echo "  help          - Show this help message"
fi