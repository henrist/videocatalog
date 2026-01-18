#!/bin/bash
set -e
cd "$(dirname "$0")/.."

output="output"
port="8000"

while [[ $# -gt 0 ]]; do
    case $1 in
        --output-dir)
            output="$2"
            shift 2
            ;;
        --port)
            port="$2"
            shift 2
            ;;
        --)
            shift
            break
            ;;
        *)
            break
            ;;
    esac
done

[[ "$output" = /* ]] || output="$(pwd)/$output"

mkdir -p "$output"

docker run --rm -it \
    -v "$output:$output" \
    -p "127.0.0.1:$port:$port" \
    videocatalog --output-dir "$output" --serve --host 0.0.0.0 --port "$port" "$@"
