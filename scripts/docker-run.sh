#!/bin/bash
set -e
cd "$(dirname "$0")/.."

output="output"
output_provided=false
cache="cache"
mounts=()

while [[ $# -gt 0 ]]; do
    case $1 in
        -m|--mount)
            mounts+=("$2")
            shift 2
            ;;
        --output-dir)
            output="$2"
            output_provided=true
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
docker_args+=(--user "$(id -u):$(id -g)")
docker_args+=(-v "$output:$output")
docker_args+=(-v "$cache:/root/.cache/huggingface")

cmd_args=()
if [ "$output_provided" = true ]; then
    cmd_args+=(--output-dir "$output")
fi

for m in "${mounts[@]}"; do
    if [[ "$m" == *:* ]]; then
        host_mount="${m%%:*}"
        container_mount="${m#*:}"
    else
        host_mount="$m"
        container_mount="$m"
    fi
    [[ "$host_mount" = /* ]] || host_mount="$(pwd)/$host_mount"
    docker_args+=(-v "$host_mount:$container_mount")
done

cmd_args+=("$@")

docker run "${docker_args[@]}" videocatalog "${cmd_args[@]}"
