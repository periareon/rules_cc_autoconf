#!/bin/bash
set -e

# calling script with fail_mod path as argument
# it assume all _hdr target should fail to build
cd "$1"

while IFS= read -r line; do
    if bazel build $line; then
        echo "$line should fall"
        exit 1
    fi
done < <(bazel query //... | grep hdr)
