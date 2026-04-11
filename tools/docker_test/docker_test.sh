#!/bin/bash
# Run Bazel tests inside a Docker container that mirrors the GitHub CI runner.
#
# Usage:
#   ./tools/docker_test/docker_test.sh                            # default targets
#   ./tools/docker_test/docker_test.sh //gnulib/tests/compat/...  # custom targets
#   ./tools/docker_test/docker_test.sh --amd64                    # emulated x86_64
#   ./tools/docker_test/docker_test.sh --amd64 //some:target      # both

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}============================================${NC}"
    echo ""
}

print_success() { echo -e "${GREEN}✓ $1${NC}"; }
print_error()   { echo -e "${RED}✗ $1${NC}"; }

# --- Parse flags -------------------------------------------------------------

PLATFORM_FLAG=""
ARCH_DESC="native"
IMAGE_NAME="rules-cc-autoconf-linux:latest"

if [[ "$1" == "--amd64" ]]; then
    PLATFORM_FLAG="--platform linux/amd64"
    ARCH_DESC="x86_64 (emulated)"
    IMAGE_NAME="rules-cc-autoconf-linux-amd64:latest"
    shift
fi

if [[ $# -eq 0 ]]; then
    TARGETS="//autoconf/... //gnulib/..."
else
    TARGETS="$*"
fi

BAZEL_CMD="test -k --curses=no --test_output=errors -- $TARGETS"

# --- Preflight ----------------------------------------------------------------

if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed or not in PATH"
    exit 1
fi
if ! docker info &> /dev/null 2>&1; then
    print_error "Docker daemon is not running"
    exit 1
fi

mkdir -p "$RESULTS_DIR"

LOG_FILE="$RESULTS_DIR/ubuntu_test_${TIMESTAMP}.log"

print_header "Docker Test (${ARCH_DESC})"
echo "Targets : $TARGETS"
echo "Log     : $LOG_FILE"
echo ""

# --- Build image --------------------------------------------------------------

NEEDS_BUILD=false
if ! docker image inspect "$IMAGE_NAME" > /dev/null 2>&1; then
    NEEDS_BUILD=true
    echo "Docker image not found, building..."
elif [[ -n "$(git -C "$WORKSPACE_ROOT" status --porcelain 2>/dev/null)" ]]; then
    echo "Uncommitted changes detected, rebuilding Docker image..."
    NEEDS_BUILD=true
fi

if $NEEDS_BUILD; then
    echo "Building Docker image (${ARCH_DESC})..."
    docker build $PLATFORM_FLAG -t "$IMAGE_NAME" \
        -f "$SCRIPT_DIR/Dockerfile.ubuntu-ci" "$WORKSPACE_ROOT"
    print_success "Docker image built successfully"
fi

# --- Run tests ----------------------------------------------------------------

echo ""
echo "Running: bazel $BAZEL_CMD"
echo "=========================================="

docker run $PLATFORM_FLAG --rm "$IMAGE_NAME" \
    bash -c "bazel $BAZEL_CMD 2>&1; echo ''; echo 'Exit code:' \$?" \
    2>&1 | tee "$LOG_FILE"

# --- Summary ------------------------------------------------------------------

print_header "Summary"

PASSED=$(grep -c "PASSED" "$LOG_FILE" 2>/dev/null || echo "0")
FAILED=$(grep -c "FAILED" "$LOG_FILE" 2>/dev/null || echo "0")

echo "Passed: $PASSED"
echo "Failed: $FAILED"
echo ""

FAILED_TESTS=$(grep -E "^//.*FAILED" "$LOG_FILE" 2>/dev/null || true)
if [[ -n "$FAILED_TESTS" ]]; then
    echo "--- Failed Tests ---"
    echo "$FAILED_TESTS"
    echo ""
fi

print_success "Full log: $LOG_FILE"

# --- Cleanup ------------------------------------------------------------------

echo ""
echo "Cleaning up Docker image..."
docker rmi "$IMAGE_NAME" 2>/dev/null || true

print_success "Done!"
