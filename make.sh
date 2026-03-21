#!/bin/bash

if [ "$1" == "setup_local" ]; then
  echo "Installing development dependencies..."
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  echo "Installing ruff..."
  uv tool install ruff@latest
  echo "Installing deploy-your-startup-cli..."
  uv tool install deploy-your-startup-cli --from .
  echo "Setup complete!"
fi

if [ "$1" == "format" ]; then
  echo "Formatting code and running ruff checks..."
  uvx ruff format
  uvx ruff check --fix
fi

if [ "$1" == "test" ]; then
  echo "Running tests..."
  uv run pytest
fi

if [ "$1" == "install_tool" ]; then
  echo "Installing deploy-your-startup-cli as a global tool..."
  uv tool install deploy-your-startup-cli --from .
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
  echo "  setup_local   - Install uv, ruff, and deploy-your-startup-cli"
  echo "  format        - Format code and run ruff checks"
  echo "  test          - Run pytest tests"
  echo "  install_tool  - Install CLI as a global tool"
  echo "  dev_install   - Install in development mode"
  echo "  clean         - Remove build artifacts"
  echo "  help          - Show this help message"
fi