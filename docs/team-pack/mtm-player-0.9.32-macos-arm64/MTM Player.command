#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
# Finder does not inherit an interactive shell's PATH.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -x ./node ]; then
  NODE=./node
elif command -v node >/dev/null 2>&1; then
  NODE="$(command -v node)"
else
  echo "Node.js 20 or newer is required. Install Node.js, then open this file again."
  read -r -p "Press Enter to close. "
  exit 1
fi
"$NODE" -e 'if(Number(process.versions.node.split(".")[0])<20)process.exit(1)' || { echo "Node.js 20 or newer is required."; exit 1; }
"$NODE" app/scripts/desktop.mjs
