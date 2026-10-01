#!/bin/sh
# Fetch the pinned third-party trees for `make rt1062-check` / `make rt1062-link`
# into firmware/build/deps (sparse, only what the build uses).
set -eu
DEPS=${1:-build/deps}
TINYUSB_REV=497709395e438add32e4189fcedbd30665c69d93
MCUX_SDK_REV=8a289764d763ad06e0c3a05c885644ed98b970af
CMSIS_REV=55b19837f5703e418ca37894d5745b1dc05e4c91
mkdir -p "$DEPS"
cd "$DEPS"

fetch() {   # dir url rev [sparse paths...]
    dir=$1 url=$2 rev=$3
    shift 3
    if [ ! -d "$dir/.git" ]; then
        git init -q "$dir"
        git -C "$dir" remote add origin "$url"
    fi
    if [ $# -gt 0 ]; then
        git -C "$dir" sparse-checkout set --no-cone "$@"
    fi
    git -C "$dir" fetch -q --depth 1 --filter=blob:none origin "$rev" 2>/dev/null ||
        git -C "$dir" fetch -q --filter=blob:none origin
    git -C "$dir" checkout -q "$rev"
}

fetch tinyusb https://github.com/hathach/tinyusb.git "$TINYUSB_REV" /src/
fetch mcux-sdk https://github.com/nxp-mcuxpresso/mcux-sdk.git "$MCUX_SDK_REV" \
    /devices/MIMXRT1062/ /drivers/common/ /drivers/enet/ /drivers/lpi2c/ /drivers/lpuart/ \
    /drivers/dcp/ /drivers/igpio/ /drivers/cache/armv7-m7/
fetch CMSIS_5 https://github.com/ARM-software/CMSIS_5.git "$CMSIS_REV" /CMSIS/Core/Include/
echo "deps ready in $DEPS"
