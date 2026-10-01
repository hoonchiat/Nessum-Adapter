# Option A bridge-MCU firmware.
#
#   make test        unit tests of the portable core (host compiler)
#   make fwsim       the core on a PC, serving the console on a pty (tool conformance)
#   make fwtool      the firmware's image verifier on a PC (signing-tool conformance)
#   make arm-check   compile the core for the RT1062 (Cortex-M7) and print its size
#   make rt1062-link build the bootloader (boot.elf) and the application (adapter.elf,
#                    adapter.bin, signed adapter-<version>.nfw for DFU) from core +
#                    platform/rt1062 + TinyUSB + SDK drivers (fetches pinned deps into
#                    build/deps). With stand-ins for the board files: proves it builds,
#                    the images do NOT boot.
#
# See platform/rt1062/README.md.

CC      ?= cc
CFLAGS  ?= -O2 -g
WARN     = -std=c11 -Wall -Wextra -Werror -Wshadow -Wstrict-prototypes
INC      = -Icore -Iplatform/host -Ithird_party/monocypher
BUILD    = build

CORE     = core/util.c core/macstore.c core/keystore.c core/mgmt.c core/ncm.c core/nline.c \
           core/fwimage.c core/fwupdate.c core/fwboot.c
MONO     = third_party/monocypher/monocypher.c third_party/monocypher/monocypher-ed25519.c
HOST     = platform/host/host_platform.c
HDRS     = $(wildcard core/*.h platform/host/*.h)
# vendored code: no -Werror (we don't patch it)
MONO_O   = $(BUILD)/monocypher.o $(BUILD)/monocypher-ed25519.o

ARM_CC   ?= arm-none-eabi-gcc
ARM_SIZE ?= arm-none-eabi-size
ARM_OBJCOPY ?= arm-none-eabi-objcopy
ARM_FLAGS = -mcpu=cortex-m7 -mthumb -mfpu=fpv5-d16 -mfloat-abi=hard -Os -ffunction-sections -fdata-sections

.PHONY: all test fwsim fwtool arm-check rt1062-deps rt1062-link clean
all: test fwsim

$(BUILD):
	mkdir -p $@

$(BUILD)/%.o: third_party/monocypher/%.c | $(BUILD)
	$(CC) $(CFLAGS) -std=c99 -c -o $@ $<

$(BUILD)/test_core: $(CORE) $(HOST) tests/test_core.c $(HDRS) $(MONO_O) | $(BUILD)
	$(CC) $(CFLAGS) $(WARN) -D_GNU_SOURCE $(INC) -o $@ $(CORE) $(HOST) tests/test_core.c $(MONO_O)

$(BUILD)/fwsim: $(CORE) $(HOST) platform/host/fwsim.c $(HDRS) $(MONO_O) | $(BUILD)
	$(CC) $(CFLAGS) $(WARN) $(INC) -o $@ $(CORE) $(HOST) platform/host/fwsim.c $(MONO_O)

$(BUILD)/fwtool: core/fwimage.c core/util.c platform/host/fwtool.c $(HDRS) $(MONO_O) | $(BUILD)
	$(CC) $(CFLAGS) $(WARN) $(INC) -o $@ core/fwimage.c core/util.c platform/host/fwtool.c $(MONO_O)

fwtool: $(BUILD)/fwtool

test: $(BUILD)/test_core
	./$(BUILD)/test_core

fwsim: $(BUILD)/fwsim

arm-check: | $(BUILD)
	@mkdir -p $(BUILD)/arm
	@for f in $(CORE); do \
	  $(ARM_CC) $(ARM_FLAGS) $(WARN) -Icore -Ithird_party/monocypher -c $$f -o $(BUILD)/arm/$$(basename $$f .c).o || exit 1; \
	done
	@for f in $(MONO); do \
	  $(ARM_CC) $(ARM_FLAGS) -std=c99 -c $$f -o $(BUILD)/arm/$$(basename $$f .c).o || exit 1; \
	done
	@$(ARM_SIZE) -t $(BUILD)/arm/*.o

# ---- RT1062 image (platform/rt1062) ----
DEPS   = $(BUILD)/deps
TUSB   = $(DEPS)/tinyusb/src
SDK    = $(DEPS)/mcux-sdk
DEV    = $(SDK)/devices/MIMXRT1062
RT_DIR = platform/rt1062
RT_SRC = $(wildcard $(RT_DIR)/*.c)
RT_CHECK_SRC = $(RT_DIR)/check/board_stubs.c
TUSB_SRC = $(TUSB)/tusb.c $(TUSB)/common/tusb_fifo.c $(TUSB)/device/usbd.c \
           $(TUSB)/class/cdc/cdc_device.c $(TUSB)/class/dfu/dfu_device.c \
           $(TUSB)/portable/chipidea/ci_hs/dcd_ci_hs.c
SDK_SRC = $(SDK)/drivers/common/fsl_common.c $(SDK)/drivers/common/fsl_common_arm.c \
          $(SDK)/drivers/enet/fsl_enet.c $(SDK)/drivers/lpi2c/fsl_lpi2c.c $(SDK)/drivers/lpuart/fsl_lpuart.c \
          $(SDK)/drivers/dcp/fsl_dcp.c $(SDK)/drivers/igpio/fsl_gpio.c $(SDK)/drivers/cache/armv7-m7/fsl_cache.c \
          $(DEV)/drivers/fsl_clock.c $(DEV)/drivers/fsl_romapi.c $(DEV)/system_MIMXRT1062.c
RT_DEFS = -DCPU_MIMXRT1062DVL6A -DXIP_EXTERNAL_FLASH=1 -DXIP_BOOT_HEADER_ENABLE=0
RT_SYS  = -isystem $(TUSB) -isystem $(DEPS)/CMSIS_5/CMSIS/Core/Include -isystem $(DEV) -isystem $(DEV)/drivers \
          $(foreach d,common enet lpi2c lpuart dcp igpio cache/armv7-m7,-isystem $(SDK)/drivers/$(d))
RT_INC  = -Icore -Ithird_party/monocypher -I$(RT_DIR) -I$(RT_DIR)/check $(RT_SYS)
RT_LDS  = $(DEV)/gcc/MIMXRT1062xxxxx_flexspi_nor.ld   # source for gen_ld.py

rt1062-deps: $(DEPS)/.ok
$(DEPS)/.ok: $(RT_DIR)/check/fetch-deps.sh
	sh $(RT_DIR)/check/fetch-deps.sh $(DEPS)
	touch $@

# Signing. The bootloader and application trust FW_SIGNING_PUB; the image is signed
# with FW_SIGNING_KEY when that file is present. Default: a development key made once
# per checkout in build/keys (never shipped). Production builds pass the offline
# key's public half and sign elsewhere:
#   make rt1062-link FW_SIGNING_PUB=prod.pub FW_SIGNING_KEY=
#   python3 host/tools/fwimage.py sign --key prod.seed --version X.Y.Z adapter.bin adapter.nfw
FWIMAGE        = python3 ../host/tools/fwimage.py
FW_SIGNING_PUB ?= $(BUILD)/keys/dev.pub
FW_SIGNING_KEY ?= $(BUILD)/keys/dev.seed
FW_VERSION     := $(shell sed -n 's/^\#define FW_VERSION "\(.*\)"/\1/p' core/mgmt.h)
RT_OUT  = $(BUILD)/rt1062
BOOT_CORE = core/fwboot.c core/fwimage.c core/util.c
BOOT_SDK  = $(SDK)/drivers/common/fsl_common.c $(SDK)/drivers/common/fsl_common_arm.c \
            $(SDK)/drivers/cache/armv7-m7/fsl_cache.c $(DEV)/drivers/fsl_romapi.c $(DEV)/system_MIMXRT1062.c
LDFLAGS_RT = -Wl,--gc-sections --specs=nano.specs --specs=nosys.specs

$(BUILD)/keys/dev.pub:
	mkdir -p $(BUILD)/keys
	$(FWIMAGE) keygen $(BUILD)/keys/dev.seed
	@echo "NOTE: development signing key created in $(BUILD)/keys - not for production"

$(RT_OUT)/fw_pubkey.c: $(FW_SIGNING_PUB)
	@mkdir -p $(RT_OUT)
	$(FWIMAGE) pubkey-c $< $@

rt1062-link: $(DEPS)/.ok $(RT_OUT)/fw_pubkey.c | $(BUILD)
	@mkdir -p $(RT_OUT)/o
	@set -e; for f in $(CORE) $(RT_SRC) $(RT_CHECK_SRC) $(RT_OUT)/fw_pubkey.c; do \
	  $(ARM_CC) $(ARM_FLAGS) $(WARN) $(RT_DEFS) $(RT_INC) -c $$f -o $(RT_OUT)/o/$$(basename $$f .c).o; \
	done
	@$(ARM_CC) $(ARM_FLAGS) $(WARN) $(RT_DEFS) $(RT_INC) -c boot/main.c -o $(RT_OUT)/o/boot_main.o
	@set -e; for f in $(MONO) $(TUSB_SRC) $(SDK_SRC); do \
	  $(ARM_CC) $(ARM_FLAGS) -std=gnu11 -w $(RT_DEFS) $(RT_INC) -c $$f -o $(RT_OUT)/o/$$(basename $$f .c).o; \
	done
	@$(ARM_CC) $(ARM_FLAGS) $(RT_DEFS) -c $(DEV)/gcc/startup_MIMXRT1062.S -o $(RT_OUT)/o/startup.o
	@python3 $(RT_DIR)/gen_ld.py app $(RT_LDS) $(RT_OUT)/app.ld
	@python3 $(RT_DIR)/gen_ld.py boot $(RT_LDS) $(RT_OUT)/boot.ld
	$(ARM_CC) $(ARM_FLAGS) -T$(RT_OUT)/app.ld $(LDFLAGS_RT) -Wl,-Map=$(RT_OUT)/adapter.map -o $(RT_OUT)/adapter.elf \
	  $$(ls $(RT_OUT)/o/*.o | grep -v '/boot_main\.o$$')
	$(ARM_CC) $(ARM_FLAGS) -T$(RT_OUT)/boot.ld $(LDFLAGS_RT) -Wl,-Map=$(RT_OUT)/boot.map -o $(RT_OUT)/boot.elf \
	  $(addprefix $(RT_OUT)/o/,boot_main.o flash_rt1062.o fw_pubkey.o startup.o monocypher.o monocypher-ed25519.o \
	    $(notdir $(BOOT_CORE:.c=.o)) $(notdir $(BOOT_SDK:.c=.o)))
	@$(ARM_SIZE) $(RT_OUT)/boot.elf $(RT_OUT)/adapter.elf
	@$(ARM_OBJCOPY) -O binary $(RT_OUT)/boot.elf $(RT_OUT)/boot.bin
	@$(ARM_OBJCOPY) -O binary $(RT_OUT)/adapter.elf $(RT_OUT)/adapter.bin
	@b=$$(stat -c %s $(RT_OUT)/boot.bin); echo "boot.bin $$b bytes (limit 65536)"; test $$b -le 65536
	@if [ -f "$(FW_SIGNING_KEY)" ]; then \
	  $(FWIMAGE) sign --key $(FW_SIGNING_KEY) --version $(FW_VERSION) $(RT_OUT)/adapter.bin $(RT_OUT)/adapter-$(FW_VERSION).nfw && \
	  $(FWIMAGE) info --pubkey $(FW_SIGNING_PUB) $(RT_OUT)/adapter-$(FW_VERSION).nfw; \
	else echo "not signed (FW_SIGNING_KEY not set): sign adapter.bin offline"; fi

clean:
	rm -rf $(BUILD)
