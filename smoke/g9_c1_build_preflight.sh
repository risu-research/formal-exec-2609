#!/usr/bin/env bash
set -euo pipefail

OUT=${1:-g9-c1-build-out}
ROOT=$(pwd)
RISCV_SHA=1150bba16a44ec4cb741982695fe1ca4cc4cdcfd
ROCKET_SHA=3ce023df7fbfc55b1eb78874b5a7780221fe242f
JAVA8_IMAGE=eclipse-temurin:8-jdk

rm -rf "$OUT" /tmp/g9-c1-riscv
mkdir -p "$OUT"

# Exact candidate source without reading candidate commit prose.
git init -q /tmp/g9-c1-riscv
git -C /tmp/g9-c1-riscv remote add origin https://github.com/YosysHQ/riscv-formal.git
git -C /tmp/g9-c1-riscv fetch -q --depth 1 origin "$RISCV_SHA"
git -C /tmp/g9-c1-riscv checkout -q --detach FETCH_HEAD
test "$(git -C /tmp/g9-c1-riscv rev-parse HEAD)" = "$RISCV_SHA"

# Exact Rocket dependency, placed at the historical relative path.
git init -q /tmp/g9-c1-riscv/cores/rocket/rocket-chip
git -C /tmp/g9-c1-riscv/cores/rocket/rocket-chip remote add origin https://github.com/chipsalliance/rocket-chip.git
git -C /tmp/g9-c1-riscv/cores/rocket/rocket-chip fetch -q --depth 1 origin "$ROCKET_SHA"
git -C /tmp/g9-c1-riscv/cores/rocket/rocket-chip checkout -q --detach FETCH_HEAD
test "$(git -C /tmp/g9-c1-riscv/cores/rocket/rocket-chip rev-parse HEAD)" = "$ROCKET_SHA"

# Only generation-relevant exact gitlinks; no riscv-tools build.
git -C /tmp/g9-c1-riscv/cores/rocket/rocket-chip submodule update --init --depth 1 hardfloat chisel3 firrtl

# Reproduce the historical RVFI monitor generation and source compatibility edit.
(
  cd /tmp/g9-c1-riscv/monitor
  python3 generate.py -p RVFIMonitor -MC
) > /tmp/g9-c1-riscv/cores/rocket/rocket-chip/vsrc/RVFIMonitor.v
sed -i '/^module/ s/\([A-Z]\+=\)/parameter &/g' /tmp/g9-c1-riscv/cores/rocket/rocket-chip/vsrc/plusarg_reader.v

# Java 8 is required by the historical SBT invocation. Preflight intentionally
# records the moving tag's resolved digest; the verdict-bearing run will pin it.
docker pull "$JAVA8_IMAGE" >/dev/null
docker image inspect "$JAVA8_IMAGE" --format '{{json .RepoDigests}}' > "$OUT/java8-repodigests.json"
docker image inspect "$JAVA8_IMAGE" --format '{{.Id}}' > "$OUT/java8-image-id.txt"
docker run --rm "$JAVA8_IMAGE" java -version > "$OUT/java8-version.txt" 2>&1

# Generate exact successor Rocket Verilog. The source tree is mounted read-write
# because SBT/FIRRTL materialize generated files and caches inside it.
docker run --rm \
  -v /tmp/g9-c1-riscv/cores/rocket/rocket-chip:/work \
  -w /work \
  -e RISCV=/tmp/riscv \
  "$JAVA8_IMAGE" \
  bash -lc 'set -euo pipefail; mkdir -p /tmp/riscv; make -C vsim verilog CONFIG=DefaultConfigWithRVFIMonitors'

GEN=/tmp/g9-c1-riscv/cores/rocket/rocket-chip/vsim/generated-src
VFILE="$GEN/rocketchip.DefaultConfigWithRVFIMonitors.v"
SRAM="$GEN/rocketchip.DefaultConfigWithRVFIMonitors.behav_srams.v"
test -s "$VFILE"
test -s "$SRAM"

# Reproduce the successor riscv-formal Rocket->RVFI IL transformation.
(
  cd /tmp/g9-c1-riscv/cores/rocket
  yosys -v2 -l rocket-chip.yslog rocket-chip.ys
)
IL=/tmp/g9-c1-riscv/cores/rocket/rocket-chip.il
test -s "$IL"

grep -q 'rvfi_trap' "$IL"
grep -q 'rvfi_valid' "$IL"

# Seal source identities and generated artifact hashes.
{
  echo "riscv_formal=$RISCV_SHA"
  echo "rocket_chip=$ROCKET_SHA"
  echo "riscv_tree=$(git -C /tmp/g9-c1-riscv rev-parse HEAD^{tree})"
  echo "rocket_tree=$(git -C /tmp/g9-c1-riscv/cores/rocket/rocket-chip rev-parse HEAD^{tree})"
  echo "hardfloat=$(git -C /tmp/g9-c1-riscv/cores/rocket/rocket-chip rev-parse HEAD:hardfloat)"
  echo "chisel3=$(git -C /tmp/g9-c1-riscv/cores/rocket/rocket-chip rev-parse HEAD:chisel3)"
  echo "firrtl=$(git -C /tmp/g9-c1-riscv/cores/rocket/rocket-chip rev-parse HEAD:firrtl)"
} > "$OUT/source-identities.txt"

yosys -V > "$OUT/yosys-version.txt"
python3 --version > "$OUT/python-version.txt" 2>&1
sha256sum \
  /tmp/g9-c1-riscv/cores/rocket/insncheck.sv \
  /tmp/g9-c1-riscv/cores/rocket/wrapper.sv \
  /tmp/g9-c1-riscv/cores/rocket/rocket-chip.ys \
  /tmp/g9-c1-riscv/checks/rvfi_insn_check.sv \
  /tmp/g9-c1-riscv/insns/isa_rv32i.txt \
  /tmp/g9-c1-riscv/monitor/generate.py \
  "$VFILE" "$SRAM" "$IL" > "$OUT/artifact-sha256.txt"

# Keep the exact generated IL for the verdict-bearing runner; compress only for transport.
gzip -c -9 "$IL" > "$OUT/rocket-chip.il.gz"
sha256sum "$OUT/rocket-chip.il.gz" > "$OUT/rocket-chip.il.gz.sha256"
wc -c "$IL" "$OUT/rocket-chip.il.gz" > "$OUT/sizes.txt"

find "$OUT" -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum > "$OUT/SHA256SUMS.txt"
echo 'G9_C1_BUILD_PREFLIGHT_PASS'
