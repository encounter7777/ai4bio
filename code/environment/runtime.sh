#!/usr/bin/env bash
# Source this file before invoking the container.
if ! command -v singularity >/dev/null && ! command -v apptainer >/dev/null; then
    module load apps/7/singularity/3.5.2 || module load tools/singularity/3.5.2
fi
if command -v apptainer >/dev/null; then
    RUNTIME=apptainer
else
    RUNTIME=singularity
fi
CODE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
WORKSPACE_DIR=$(dirname "$CODE_DIR")
IMAGE="$CODE_DIR/environment/struct2sl-cu118.sif"

container() {
    export SINGULARITYENV_PYTHONPATH=/mnt/code/environment/python
    export APPTAINERENV_PYTHONPATH=/mnt/code/environment/python
    export SINGULARITYENV_OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
    export APPTAINERENV_OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
    local flags=()
    if [[ ${USE_GPU:-0} == 1 ]]; then
        export SINGULARITYENV_CUDA_VISIBLE_DEVICES="$CUDA_VISIBLE_DEVICES"
        export APPTAINERENV_CUDA_VISIBLE_DEVICES="$CUDA_VISIBLE_DEVICES"
        flags+=(--nv)
    fi
    "$RUNTIME" exec --cleanenv --bind "$WORKSPACE_DIR:/mnt" --pwd /mnt/code ${flags[@]+"${flags[@]}"} "$IMAGE" "$@"
}
