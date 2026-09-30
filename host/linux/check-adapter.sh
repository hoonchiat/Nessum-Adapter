#!/bin/sh
# Bring-up sanity check for the USB <-> Nessum adapter on the Linux host.
# Handles the selected option C (asix + cp210x) and option A (cdc_ncm + cdc_acm).
# Usage: check-adapter.sh [iface]   (default iface: eth2)
set -u
IFACE="${1:-eth2}"
rc=0
drv=""

say()  { printf '%s\n' "$*"; }
fail() { say "FAIL: $*"; rc=1; }
ok()   { say "ok:   $*"; }

say "== Host"
say "$(uname -srm)"
grep -m1 -E 'CPU part|model name' /proc/cpuinfo 2>/dev/null || true

say "== Kernel drivers"
have=0
for m in asix cdc_ncm; do
  if [ -d /sys/module/$m ] || modprobe -n $m 2>/dev/null; then
    ok "$m available"
    have=1
  fi
done
[ "$have" = 1 ] || fail "neither asix (option C) nor cdc_ncm (option A) available - see options-bc/kernel.config"

say "== USB enumeration"
found=0
for d in /sys/bus/usb/devices/*; do
  [ -f "$d/speed" ] || continue
  for i in "$d":*; do
    [ -e "$i/driver" ] || continue
    name=$(basename "$(readlink "$i/driver")")
    if [ "$name" = "asix" ] || [ "$name" = "cdc_ncm" ]; then
      found=1
      drv=$name
      spd=$(cat "$d/speed")
      say "device $(cat "$d/idVendor"):$(cat "$d/idProduct") '$(cat "$d/product" 2>/dev/null)' driver=$name speed=${spd}Mb/s"
      [ "$spd" = "480" ] && ok "High-Speed link" || fail "not High-Speed (got ${spd}Mb/s) - check cable/hub"
    fi
  done
done
[ "$found" = 1 ] || fail "no asix / cdc_ncm adapter bound"

say "== Management port"
if [ -e /dev/nessum-mgmt ]; then
  ok "/dev/nessum-mgmt present -> $(readlink -f /dev/nessum-mgmt)"
  if [ "$drv" = "cdc_ncm" ] && command -v nessumctl >/dev/null 2>&1; then
    say "adapter MAC (active): $(nessumctl mac get -q 2>/dev/null || echo '?')"
  fi
else
  fail "/dev/nessum-mgmt missing - cp210x (option C) or cdc_acm (option A) loaded and udev rules installed?"
fi

say "== Network interface $IFACE"
if [ -d "/sys/class/net/$IFACE" ]; then
  ok "present, mac $(cat "/sys/class/net/$IFACE/address")"
  say "type $(cat "/sys/class/net/$IFACE/type") (1 = Ethernet), mtu $(cat "/sys/class/net/$IFACE/mtu")"
  if [ "$drv" = "asix" ]; then
    say "note: option C - carrier is always up (Reverse-RMII); read the Nessum link state over /dev/nessum-mgmt"
  elif [ "$(cat "/sys/class/net/$IFACE/carrier" 2>/dev/null)" = "1" ]; then
    ok "carrier up (joined a Nessum network)"
  else
    say "note: no carrier - adapter has not joined a Nessum network yet"
  fi
else
  fail "interface $IFACE not found (udev naming rule installed?)"
fi

exit $rc
