#!/usr/bin/env bash
# Runs ONE PicoRV32 instruction test (tests/<name>.S) as its own isolated
# simulation, using the SINGLE_TEST_ONLY hook added to firmware/start_single.S.
#
# This exists because the stock harness links all 45 tests into one linear
# firmware that halts permanently on the first failure (see README's Phase 4
# section for how that was discovered) — this script gives real per-test
# isolation instead, so a real regression can have independent PASS/FAIL per
# test the way regression_parser.py expects.
#
# Usage: run_single_picorv32_test.sh <picorv32_dir> <test_name> <toolchain_prefix>
# Prints one line to stdout in our TEST:/SEED:/STATUS:/TIME: format.
set -euo pipefail

PICORV32_DIR="$1"
TEST_NAME="$2"
TOOLCHAIN_PREFIX="${3:-riscv64-unknown-elf-}"

cd "$PICORV32_DIR"

if [ ! -f testbench.vvp ]; then
  echo "ERROR: testbench.vvp not built yet in $PICORV32_DIR (run: iverilog -o testbench.vvp testbench.v picorv32.v)" >&2
  exit 2
fi

if [ ! -f firmware/start_single.S ]; then
  echo "ERROR: firmware/start_single.S not found — see README Phase 4 section" >&2
  exit 2
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

"${TOOLCHAIN_PREFIX}gcc" -c -mabi=ilp32 -march=rv32im -o "$WORK/start_single.o" \
  -DSINGLE_TEST_ONLY -DSINGLE_TEST_NAME="$TEST_NAME" firmware/start_single.S

"${TOOLCHAIN_PREFIX}gcc" -c -mabi=ilp32 -march=rv32im -o "$WORK/${TEST_NAME}.o" \
  -DTEST_FUNC_NAME="$TEST_NAME" -DTEST_FUNC_TXT="\"$TEST_NAME\"" -DTEST_FUNC_RET="${TEST_NAME}_ret" \
  "tests/${TEST_NAME}.S"

for f in irq print hello sieve multest stats; do
  if [ -f "firmware/$f.c" ]; then
    "${TOOLCHAIN_PREFIX}gcc" -c -mabi=ilp32 -march=rv32i -Os --std=c99 -ffreestanding -nostdlib \
      -o "$WORK/$f.o" "firmware/$f.c"
  else
    "${TOOLCHAIN_PREFIX}gcc" -c -mabi=ilp32 -march=rv32im -o "$WORK/$f.o" "firmware/$f.S"
  fi
done

"${TOOLCHAIN_PREFIX}gcc" -Os -mabi=ilp32 -march=rv32imc -ffreestanding -nostdlib \
  -o "$WORK/firmware.elf" \
  -Wl,--build-id=none,-Bstatic,-T,firmware/sections.lds,-Map,"$WORK/firmware.map",--strip-debug \
  "$WORK/start_single.o" "$WORK/${TEST_NAME}.o" "$WORK/irq.o" "$WORK/print.o" "$WORK/hello.o" \
  "$WORK/sieve.o" "$WORK/multest.o" "$WORK/stats.o" -lgcc

"${TOOLCHAIN_PREFIX}objcopy" -O binary "$WORK/firmware.elf" "$WORK/firmware.bin"
python3 firmware/makehex.py "$WORK/firmware.bin" 32768 > "$WORK/firmware.hex"

# testbench.v hardcodes the path "firmware/firmware.hex" via $readmemh, so we
# must actually write there. Back up any real one first.
BACKUP=""
if [ -f firmware/firmware.hex ]; then
  BACKUP=$(mktemp)
  cp firmware/firmware.hex "$BACKUP"
fi
cp "$WORK/firmware.hex" firmware/firmware.hex

START=$(date +%s.%N)
SIM_OUT=$(timeout 30 vvp -N testbench.vvp +noerror 2>&1 || true)
END=$(date +%s.%N)

if [ -n "$BACKUP" ]; then
  cp "$BACKUP" firmware/firmware.hex
  rm -f "$BACKUP"
fi

RUNTIME=$(echo "$END - $START" | bc)

if echo "$SIM_OUT" | grep -q "^${TEST_NAME}\.\.OK"; then
  STATUS="PASSED"
elif echo "$SIM_OUT" | grep -q "^${TEST_NAME}\.\.ERROR"; then
  STATUS="FAILED"
elif echo "$SIM_OUT" | grep -q "TIMEOUT"; then
  STATUS="ERROR"
else
  STATUS="ERROR"
fi

# Honest note: this testbench is fully deterministic (no $random seeding),
# unlike a real UVM regression. SEED is N/A rather than a fabricated number —
# regression_parser.py already handles a non-numeric seed field (-> seed=None).
printf "TEST: uvm_test_picorv32_%s  SEED: N/A  STATUS: %s  TIME: %.1fs\n" \
  "$TEST_NAME" "$STATUS" "$RUNTIME"

# Emit the raw sim output on stderr for debugging / log capture, tagged so a
# caller can separate it from the one-line summary on stdout.
echo "--- raw sim output for $TEST_NAME ---" >&2
echo "$SIM_OUT" >&2
