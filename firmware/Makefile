# Option A bridge-MCU firmware.
#
#   make test        unit tests of the portable core (host compiler)
#   make fwsim       the core on a PC, serving the console on a pty (tool conformance)
#   make arm-check   compile the core for the RT1062 (Cortex-M7) and print its size
#   make rt1062-link compile platform/rt1062 + core + TinyUSB + SDK drivers and link
#                    them (fetches pinned deps into build/deps). With stand-ins for
#                    the board files: proves it builds, the image does NOT boot.
#
# See platform/rt1062/README.md.

CC      ?= cc
CFLAGS  ?= -O2 -g
WARN     = -std=c11 -Wall -Wextra -Werror -Wshadow -Wstrict-prototypes
INC      = -Icore -Iplatform/host
BUILD    = build

CORE     = core/util.c core/macstore.c core/keystore.c core/mgmt.c core/ncm.c core/nline.c
HOST     = platform/host/host_platform.c
HDRS     = $(wildcard core/*.h platform/host/*.h)

ARM_CC   ?= arm-none-eabi-gcc
ARM_SIZE ?= arm-none-eabi-size
ARM_FLAGS = -mcpu=cortex-m7 -mthumb -mfpu=fpv5-d16 -mfloat-abi=hard -Os -ffunction-sections -fdata-sections

.PHONY: all test fwsim arm-check rt1062-deps rt1062-link clean
all: test fwsim

$(BUILD):
	mkdir -p $@

$(BUILD)/test_core: $(CORE) $(HOST) tests/test_core.c $(HDRS) | $(BUILD)
	$(CC) $(CFLAGS) $(WARN) -D_GNU_SOURCE $(INC) -o $@ $(CORE) $(HOST) tests/test_core.c

$(BUILD)/fwsim: $(CORE) $(HOST) platform/host/fwsim.c $(HDRS) | $(BUILD)
	$(CC) $(CFLAGS) $(WARN) $(INC) -o $@ $(CORE) $(HOST) platform/host/fwsim.c

test: $(BUILD)/test_core
	./$(BUILD)/test_core

fwsim: $(BUILD)/fwsim

arm-check: | $(BUILD)
	@mkdir -p $(BUILD)/arm
	@for f in $(CORE); do \
	  $(ARM_CC) $(ARM_FLAGS) $(WARN) -Icore -c $$f -o $(BUILD)/arm/$$(basename $$f .c).o || exit 1; \
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
           $(TUSB)/class/cdc/cdc_device.c $(TUSB)/portable/chipidea/ci_hs/dcd_ci_hs.c
SDK_SRC = $(SDK)/drivers/common/fsl_common.c $(SDK)/drivers/common/fsl_common_arm.c \
          $(SDK)/drivers/enet/fsl_enet.c $(SDK)/drivers/lpi2c/fsl_lpi2c.c $(SDK)/drivers/lpuart/fsl_lpuart.c \
          $(SDK)/drivers/dcp/fsl_dcp.c $(SDK)/drivers/igpio/fsl_gpio.c $(SDK)/drivers/cache/armv7-m7/fsl_cache.c \
          $(DEV)/drivers/fsl_clock.c $(DEV)/drivers/fsl_romapi.c $(DEV)/system_MIMXRT1062.c
RT_DEFS = -DCPU_MIMXRT1062DVL6A -DXIP_EXTERNAL_FLASH=1 -DXIP_BOOT_HEADER_ENABLE=0
RT_SYS  = -isystem $(TUSB) -isystem $(DEPS)/CMSIS_5/CMSIS/Core/Include -isystem $(DEV) -isystem $(DEV)/drivers \
          $(foreach d,common enet lpi2c lpuart dcp igpio cache/armv7-m7,-isystem $(SDK)/drivers/$(d))
RT_INC  = -Icore -I$(RT_DIR) -I$(RT_DIR)/check $(RT_SYS)
RT_LDS  = $(DEV)/gcc/MIMXRT1062xxxxx_flexspi_nor.ld

rt1062-deps: $(DEPS)/.ok
$(DEPS)/.ok: $(RT_DIR)/check/fetch-deps.sh
	sh $(RT_DIR)/check/fetch-deps.sh $(DEPS)
	touch $@

rt1062-link: $(DEPS)/.ok | $(BUILD)
	@mkdir -p $(BUILD)/rt1062
	@set -e; for f in $(CORE) $(RT_SRC) $(RT_CHECK_SRC); do \
	  $(ARM_CC) $(ARM_FLAGS) $(WARN) $(RT_DEFS) $(RT_INC) -c $$f -o $(BUILD)/rt1062/$$(basename $$f .c).o; \
	done
	@set -e; for f in $(TUSB_SRC) $(SDK_SRC); do \
	  $(ARM_CC) $(ARM_FLAGS) -std=gnu11 -w $(RT_DEFS) $(RT_INC) -c $$f -o $(BUILD)/rt1062/3p_$$(basename $$f .c).o; \
	done
	$(ARM_CC) $(ARM_FLAGS) $(RT_DEFS) -c $(DEV)/gcc/startup_MIMXRT1062.S -o $(BUILD)/rt1062/startup.o
	$(ARM_CC) $(ARM_FLAGS) -T$(RT_LDS) -Wl,--gc-sections -Wl,-Map=$(BUILD)/rt1062/adapter.map \
	  --specs=nano.specs --specs=nosys.specs -o $(BUILD)/rt1062/adapter.elf $(BUILD)/rt1062/*.o
	@$(ARM_SIZE) $(BUILD)/rt1062/adapter.elf

clean:
	rm -rf $(BUILD)
