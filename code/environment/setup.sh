#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source environment/runtime.sh
export USE_GPU=0
exec 9>environment/.setup.lock
flock 9
if [[ ! -s "$IMAGE" ]]; then
    (
        build_tmp=${STRUCT2SL_BUILD_TMP:-"$CODE_DIR/environment/.build-tmp"}
        build_cache=${STRUCT2SL_BUILD_CACHE:-"$CODE_DIR/environment/.build-cache"}
        mkdir -p "$build_tmp" "$build_cache"
        build_tmp=$(cd "$build_tmp" && pwd -P)
        build_cache=$(cd "$build_cache" && pwd -P)
        export TMPDIR="$build_tmp" SINGULARITY_TMPDIR="$build_tmp" APPTAINER_TMPDIR="$build_tmp"
        export SINGULARITY_CACHEDIR="$build_cache" APPTAINER_CACHEDIR="$build_cache"
        df -h "$build_tmp" "$build_cache" "$(dirname "$IMAGE")"
        staging=$(mktemp -d "$(dirname "$IMAGE")/.sif-build.XXXXXXXX")
        trap 'rm -rf -- "$staging"' EXIT
        "$RUNTIME" pull "$staging/struct2sl.sif" docker://pytorch/pytorch:2.3.1-cuda11.8-cudnn8-runtime
        mv -- "$staging/struct2sl.sif" "$IMAGE"
    )
fi
if [[ ! -d environment/upstream/.git ]]; then
    git clone https://github.com/hyr-hit/Struct2SL environment/upstream
    if [[ -n ${UPSTREAM_REVISION:-} ]]; then
        git -C environment/upstream checkout --detach "$UPSTREAM_REVISION"
    fi
fi
if [[ -n ${UPSTREAM_REVISION:-} && "$(git -C environment/upstream rev-parse HEAD)" != "$UPSTREAM_REVISION" ]]; then
    echo "Existing upstream checkout does not match UPSTREAM_REVISION" >&2
    exit 1
fi
git -C environment/upstream rev-parse HEAD | tee environment/upstream-revision.txt
container python -m pip install --disable-pip-version-check --upgrade --target /mnt/code/environment/python -r environment/requirements.txt
container python -c 'import torch, numpy, pandas, scipy, sklearn, matplotlib; assert torch.__version__.split("+")[0] == "2.3.1"; assert torch.version.cuda == "11.8"; print(torch.__version__, torch.version.cuda)'
container python -m pip freeze > environment/resolved-packages.txt
sha256sum "$IMAGE" > environment/image.sha256
