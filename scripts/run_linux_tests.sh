#!/bin/bash
# Script to run bazel tests in a Linux Docker container and update golden files

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT"

echo "Building Docker image (x86_64 Linux)..."
docker build --platform linux/amd64 -t rules_cc_autoconf_linux -f Dockerfile .

echo "Running tests in Docker container (x86_64 Linux via emulation)..."
# Run the container with gnulib directory mounted so update.py writes directly to host
# Use --platform to force x86_64 for consistent Linux results
docker run --rm \
    --platform linux/amd64 \
    -v "$REPO_ROOT/gnulib:/workspace/gnulib" \
    -v "$REPO_ROOT/update.py:/workspace/update.py:ro" \
    rules_cc_autoconf_linux

echo "Done! Check git diff for updated golden files."
