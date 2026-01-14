# Dockerfile for running bazel tests on Linux
FROM ubuntu:22.04

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    git \
    python3 \
    python3-pip \
    build-essential \
    autoconf \
    automake \
    libtool \
    pkg-config \
    gettext \
    m4 \
    && rm -rf /var/lib/apt/lists/*

# Install Bazelisk (manages Bazel versions automatically)
# Using amd64 binary since we run with --platform linux/amd64
RUN curl -fsSL https://github.com/bazelbuild/bazelisk/releases/download/v1.19.0/bazelisk-linux-amd64 -o /usr/local/bin/bazel \
    && chmod +x /usr/local/bin/bazel

# Set up workspace
WORKDIR /workspace

# Copy the repository (excluding bazel output directories)
COPY . .

# Set up git safe directory (needed for Bazel)
RUN git config --global --add safe.directory /workspace

# Create entrypoint script
RUN printf '#!/bin/bash\n\
set -e\n\
echo "Running bazel test //... -k"\n\
bazel test //... -k --test_output=errors 2>&1 | tee /workspace/test_output.log || true\n\
echo ""\n\
echo "Running update.py to extract test results..."\n\
python3 /workspace/update.py\n\
echo "Done!"\n' > /entrypoint.sh && chmod +x /entrypoint.sh

# Default command
CMD ["/entrypoint.sh"]
