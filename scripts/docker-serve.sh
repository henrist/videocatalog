#!/bin/bash
set -e
cd "$(dirname "$0")/.."

output="output"
port="8000"
mounts=()

while [[ $# -gt 0 ]]; do
    case $1 in
        -m|--mount)
            mounts+=("$2")
            shift 2
            ;;
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

docker_args=(--rm -it)
docker_args+=(--user "$(id -u):$(id -g)")
docker_args+=(-v "$output:$output")
docker_args+=(-p "127.0.0.1:$port:$port")

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

docker run "${docker_args[@]}" videocatalog serve --output-dir "$output" --host 0.0.0.0 --port "$port" "$@"
