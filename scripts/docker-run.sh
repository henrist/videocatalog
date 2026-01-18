#!/bin/bash
set -e
cd "$(dirname "$0")/.."

output="output"
cache="cache"
mount=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -m|--mount)
            mount="$2"
            shift 2
            ;;
        --output-dir)
            output="$2"
            shift 2
            ;;
        --cache-dir)
            cache="$2"
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
[[ "$cache" = /* ]] || cache="$(pwd)/$cache"

mkdir -p "$output" "$cache"

docker_args=(--rm)
docker_args+=(-v "$output:$output")
docker_args+=(-v "$cache:/root/.cache/huggingface")

cmd_args=(--output-dir "$output")

if [ -n "$mount" ]; then
    host_mount="$mount"
    [[ "$host_mount" = /* ]] || host_mount="$(pwd)/$host_mount"
    docker_args+=(-v "$host_mount:$mount")
fi

cmd_args+=("$@")

docker run "${docker_args[@]}" videocatalog "${cmd_args[@]}"
