#!/bin/bash
set -e
cd "$(dirname "$0")/.."

output="${OUTPUT:-output}"

rm -rf "$output"/*/ "$output"/gallery.html
