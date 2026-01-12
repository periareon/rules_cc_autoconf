#!/usr/bin/env bash

set -euo pipefail

CURRENT_DIR=/Users/andrebrisco/Code/rules_cc_autoconf/

find "${CURRENT_DIR}" -name "*.c" -exec clang-format -i {} \;
# Exclude golden_*.h files as they contain @VAR@ placeholders that clang-format mishandles
find "${CURRENT_DIR}" -name "*.h" -exec clang-format -i {} \;
find "${CURRENT_DIR}" -name "*.cc" -exec clang-format -i {} \;
buildifier -lint=fix -mode=fix -warnings=all -r "${CURRENT_DIR}"
echo "Done"
