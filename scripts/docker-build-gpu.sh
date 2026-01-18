#!/bin/bash
set -e
cd "$(dirname "$0")/.."
docker build -f Dockerfile.cuda -t videocatalog-gpu .
