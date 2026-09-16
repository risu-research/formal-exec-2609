#!/usr/bin/env bash
set -euo pipefail

OUT=${1:-g9-c1-build-out}
ROOT=$(pwd)
RISCV_SHA=1150bba16a44ec4cb741982695fe1ca4cc4cdcfd
ROCKET_SHA=3ce023df7fbfc55b1eb78874b5a7780221fe242f
JAVA8_IMAGE='eclipse-temurin@sha256:5759e5329a0983257ca7276540563f4559704f12753ecfb56b1b1794eb2b37b3'

rm -rf "$OUT" /tmp/g9-c1-riscv
mkdir -p "$OUT"
OUT_ABS=$(readlink -f "$OUT")

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

# Reconstruct the root vsrc alias expected by the historical riscv-formal build.
ROCKET=/tmp/g9-c1-riscv/cores/rocket/rocket-chip
test -d "$ROCKET/src/main/resources/vsrc"
test -f "$ROCKET/src/main/resources/vsrc/plusarg_reader.v"
test ! -e "$ROCKET/vsrc"
ln -s src/main/resources/vsrc "$ROCKET/vsrc"
test -L "$ROCKET/vsrc"
test "$(readlink "$ROCKET/vsrc")" = 'src/main/resources/vsrc'

# Reproduce the historical RVFI monitor generation and source compatibility edit.
(
  cd /tmp/g9-c1-riscv/monitor
  python3 generate.py -p RVFIMonitor -MC
) > "$ROCKET/vsrc/RVFIMonitor.v"
sed -i '/^module/ s/\([A-Z]\+=\)/parameter &/g' "$ROCKET/vsrc/plusarg_reader.v"

test -s "$ROCKET/src/main/resources/vsrc/RVFIMonitor.v"
grep -q '^module plusarg_reader #(parameter' "$ROCKET/src/main/resources/vsrc/plusarg_reader.v"

# Exact Java 8 runtime learned from the second build-only RED.
docker pull "$JAVA8_IMAGE" >/dev/null
docker image inspect "$JAVA8_IMAGE" --format '{{json .RepoDigests}}' > "$OUT/java8-repodigests.json"
docker image inspect "$JAVA8_IMAGE" --format '{{.Id}}' > "$OUT/java8-image-id.txt"
docker run --rm "$JAVA8_IMAGE" java -version > "$OUT/java8-version.txt" 2>&1

grep -q '5759e5329a0983257ca7276540563f4559704f12753ecfb56b1b1794eb2b37b3' "$OUT/java8-repodigests.json"

# Generate exact successor Rocket Verilog. Install only the missing build utility
# inside the exact pinned runtime; record the package and make versions.
docker run --rm \
  -v "$ROCKET":/work \
  -v "$OUT_ABS":/evidence \
  -w /work \
  -e RISCV=/tmp/riscv \
  "$JAVA8_IMAGE" \
  bash -lc 'set -euo pipefail; apt-get update >/dev/null; DEBIAN_FRONTEND=noninteractive apt-get install -y make >/dev/null; dpkg-query -W -f="\${Package}=\${Version}\n" make > /evidence/container-make-package.txt; make --version > /evidence/container-make-version.txt; mkdir -p /tmp/riscv; make -C vsim verilog CONFIG=DefaultConfigWithRVFIMonitors'

GEN="$ROCKET/vsim/generated-src"
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
  echo "rocket_tree=$(git -C "$ROCKET" rev-parse HEAD^{tree})"
  echo "hardfloat=$(git -C "$ROCKET" rev-parse HEAD:hardfloat)"
  echo "chisel3=$(git -C "$ROCKET" rev-parse HEAD:chisel3)"
  echo "firrtl=$(git -C "$ROCKET" rev-parse HEAD:firrtl)"
  echo "root_vsrc_link=$(readlink "$ROCKET/vsrc")"
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
  "$ROCKET/src/main/resources/vsrc/plusarg_reader.v" \
  "$ROCKET/src/main/resources/vsrc/RVFIMonitor.v" \
  "$VFILE" "$SRAM" "$IL" > "$OUT/artifact-sha256.txt"

# Keep the exact generated IL for the verdict-bearing runner; compress only for transport.
gzip -c -9 "$IL" > "$OUT/rocket-chip.il.gz"
sha256sum "$OUT/rocket-chip.il.gz" > "$OUT/rocket-chip.il.gz.sha256"
wc -c "$IL" "$OUT/rocket-chip.il.gz" > "$OUT/sizes.txt"

find "$OUT" -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum > "$OUT/SHA256SUMS.txt"
echo 'G9_C1_BUILD_PREFLIGHT_PASS'
