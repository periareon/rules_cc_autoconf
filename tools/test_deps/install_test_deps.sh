#!/usr/bin/env bash

set -euo pipefail

if [ "$(uname)" == "Darwin" ]; then
    brew install autoconf
else
    sudo apt install autoconf -y
fi
