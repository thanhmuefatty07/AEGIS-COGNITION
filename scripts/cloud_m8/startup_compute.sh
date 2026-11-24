#!/usr/bin/env bash
# AEGIS Remote Cloud Compute Runner (startup_compute.sh v2)
# Executes NV-001, NV-005, NV-007, NV-008-L, NV-013, NV-014, NV-017, NV-020 on
# pinned CPython 3.14.7 (via uv) and pinned Rust from rust-toolchain.toml (1.99.1).
set -euo pipefail

export HOME="/root"
export USER="root"

LOG_FILE="/var/log/aegis_m8_startup.log"
mkdir -p /var/log
exec > >(tee -a "${LOG_FILE}" /dev/console) 2>&1

echo "================================================================================"
echo "  AEGIS Cloud Research & Verification Runner v2"
echo "  Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "================================================================================"

get_metadata() {
    local key="$1"
    local default_val="${2:-}"
    curl -s -f -H "Metadata-Flavor: Google" \
        "http://metadata.google.internal/computeMetadata/v1/instance/attributes/${key}" \
        2>/dev/null || echo "${default_val}"
}

GCS_BUCKET=$(get_metadata "gcs_bucket" "aegis-research-artifacts-2026")
GCS_RUN_PREFIX=$(get_metadata "gcs_run_prefix" "")
TIER=$(get_metadata "tier" "h2")
TASK=$(get_metadata "task" "verification")
HEAD_SHA=$(get_metadata "head_sha" "HEAD")
RUN_ID=$(get_metadata "run_id" "manual-$(date +%s)")
ARTIFACT_REGISTRY=$(get_metadata "artifact_registry" "us-central1-docker.pkg.dev/aegis-verification-2026/aegis-research-images")
FUZZ_MAX_TOTAL_TIME=$(get_metadata "fuzz_max_total_time" "300")
FUZZ_RUNS=$(get_metadata "fuzz_runs" "0")
SCCACHE_PREFIX=$(get_metadata "sccache_prefix" "")
SECRET_NAMES=$(get_metadata "secret_names" "")

VM_NAME=$(curl -s -f -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/name" 2>/dev/null || hostname)
ZONE=$(curl -s -f -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/zone" 2>/dev/null | awk -F/ '{print $NF}' || echo "us-central1-a")
PROJECT_ID=$(curl -s -f -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/project/project-id" 2>/dev/null || get_metadata "project_id" "aegis-verification-2026")

if [ -z "${GCS_RUN_PREFIX}" ]; then
    GCS_RUN_PREFIX="runs/${RUN_ID}"
fi
GCS_RUN_PREFIX="${GCS_RUN_PREFIX#/}"
GCS_RUN_PREFIX="${GCS_RUN_PREFIX%/}"
GCS_RUN_URI="gs://${GCS_BUCKET}/${GCS_RUN_PREFIX}"

echo "[+] Execution context:"
echo "    VM Name:           ${VM_NAME}"
echo "    Zone:              ${ZONE}"
echo "    Project:           ${PROJECT_ID}"
echo "    Tier:              ${TIER}"
echo "    GCS Run URI:       ${GCS_RUN_URI}"
echo "    Artifact Registry: ${ARTIFACT_REGISTRY}"
echo "    Run ID:            ${RUN_ID}"
echo "    Fuzz Budget (s):   ${FUZZ_MAX_TOTAL_TIME}"

WORKSPACE="/opt/aegis"
EVIDENCE_DIR="/tmp/evidence"
mkdir -p "${WORKSPACE}" "${EVIDENCE_DIR}"

# ------------------------------------------------------------------------------
# Emergency & Exit Teardown Trap
# Guarantees diagnostics upload to run-scoped GCS URI and immediate VM deletion.
# ------------------------------------------------------------------------------
TEARDOWN_DONE=0
teardown_self() {
    local exit_code=$?
    if [ "${TEARDOWN_DONE}" -ne 0 ]; then
        return
    fi
    TEARDOWN_DONE=1
    trap - EXIT ERR SIGTERM SIGINT

    echo "================================================================================"
    echo "  AEGIS Cloud VM Teardown Handler (Exit code: ${exit_code})"
    echo "  Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "================================================================================"

    if [ "${exit_code}" -ne 0 ] && [ -n "${GCS_BUCKET}" ]; then
        echo "[!] Execution terminated with error ${exit_code}. Uploading diagnostics..."
        echo "FAILED ${exit_code} $(date -u +"%Y-%m-%dT%H:%M:%SZ")" > /tmp/FAILURE
        gcloud storage cp /tmp/FAILURE "${GCS_RUN_URI}/FAILURE-${VM_NAME}" 2>/dev/null || true

        if [ -f "${LOG_FILE}" ]; then
            gcloud storage cp "${LOG_FILE}" "${GCS_RUN_URI}/startup-failure-${VM_NAME}.log" 2>/dev/null || true
        fi
    fi

    echo "[+] Deleting VM instance: ${VM_NAME} (Zone: ${ZONE}, Project: ${PROJECT_ID})..."
    local delete_args=("compute" "instances" "delete" "${VM_NAME}" "--zone=${ZONE}" "--quiet")
    if [ -n "${PROJECT_ID}" ]; then
        delete_args+=("--project=${PROJECT_ID}")
    fi

    sync
    sleep 3
    gcloud "${delete_args[@]}" || shutdown -h now || true
}

trap 'teardown_self' EXIT ERR SIGTERM SIGINT

# 1. System packages & Artifact Registry Docker auth
echo "[+] Step 1: Installing minimal base system dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -q
apt-get install -y -q \
    build-essential \
    curl \
    git \
    pkg-config \
    libssl-dev \
    clang \
    llvm \
    ca-certificates \
    docker.io \
    jq

if [ -n "${ARTIFACT_REGISTRY}" ]; then
    AR_HOST="${ARTIFACT_REGISTRY%%/*}"
    gcloud auth configure-docker "${AR_HOST}" --quiet || true
fi

# 2. Unpack Repository from unique run-scoped GCS URI (before toolchain pin lookup)
echo "[+] Step 2: Unpacking repository into ${WORKSPACE}..."
if [ -f "/tmp/repo.tar.gz" ]; then
    tar -xzf /tmp/repo.tar.gz -C "${WORKSPACE}"
elif [ -n "${GCS_BUCKET}" ]; then
    echo "[+] Downloading repository archive from ${GCS_RUN_URI}/repo.tar.gz..."
    gcloud storage cp "${GCS_RUN_URI}/repo.tar.gz" /tmp/repo.tar.gz
    tar -xzf /tmp/repo.tar.gz -C "${WORKSPACE}"
else
    echo "[-] No repository archive found at /tmp/repo.tar.gz or ${GCS_RUN_URI}/repo.tar.gz."
    exit 1
fi

cd "${WORKSPACE}"

# 3. Runtime Secret Manager Injection (names only in metadata; never persisted/logged)
if [ -n "${SECRET_NAMES}" ]; then
    echo "[+] Step 3: Loading requested secret names from GCP Secret Manager into process env..."
    IFS='|' read -ra SEC_LIST <<< "${SECRET_NAMES}"
    for sec_name in "${SEC_LIST[@]}"; do
        if [ -z "${sec_name}" ]; then
            continue
        fi
        env_var=$(printf '%s' "${sec_name}" | tr '[:lower:]-' '[:upper:]_')
        if val=$(gcloud secrets versions access latest --secret="${sec_name}" --project="${PROJECT_ID}" 2>/dev/null); then
            export "${env_var}=${val}"
            echo "    [OK] Loaded secret resource '${sec_name}' into \$${env_var} (value redacted)"
        else
            echo "    [WARN] Secret '${sec_name}' unavailable in Secret Manager; leaving \$${env_var} unset"
        fi
    done
fi

# 4. Pinned Rust Toolchain from rust-toolchain.toml + sccache
echo "[+] Step 4: Configuring pinned Rust toolchain and sccache..."
export RUSTUP_HOME="/root/.rustup"
export CARGO_HOME="/root/.cargo"
export PATH="${CARGO_HOME}/bin:/root/.local/bin:${PATH}"

RUST_CHANNEL="1.99.1"
if [ -f "${WORKSPACE}/rust-toolchain.toml" ]; then
    PARSED_CHANNEL=$(sed -n 's/^[[:space:]]*channel[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "${WORKSPACE}/rust-toolchain.toml" | head -n 1)
    if [ -n "${PARSED_CHANNEL}" ]; then
        RUST_CHANNEL="${PARSED_CHANNEL}"
    fi
fi
echo "    Pinned Rust toolchain: ${RUST_CHANNEL}"

if ! command -v rustup &>/dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain "${RUST_CHANNEL}" --profile minimal --component rustfmt,clippy
fi

[ -f "${CARGO_HOME}/env" ] && source "${CARGO_HOME}/env" || true
rustup toolchain install "${RUST_CHANNEL}" --profile minimal --component rustfmt,clippy
rustup default "${RUST_CHANNEL}"
rustup toolchain install nightly --profile minimal --component miri,rust-src,llvm-tools-preview
cargo install --locked cargo-fuzz || true

if command -v sccache &>/dev/null || cargo install --locked sccache >/dev/null 2>&1; then
    export SCCACHE_DIR="/opt/sccache"
    mkdir -p "${SCCACHE_DIR}"
    if [ -n "${SCCACHE_PREFIX}" ]; then
        gcloud storage rsync "${SCCACHE_PREFIX}" "${SCCACHE_DIR}" --quiet >/dev/null 2>&1 || true
    fi
    export RUSTC_WRAPPER="sccache"
fi

# 5. Pinned uv + CPython 3.14.7 Environment (NO Python 3.11 syntax rewriting!)
echo "[+] Step 5: Installing uv and pinned CPython 3.14.7..."
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="/root/.local/bin:${PATH}"
fi

uv python install 3.14.7
uv venv --python 3.14.7 /opt/venv
export VIRTUAL_ENV="/opt/venv"
export PATH="/opt/venv/bin:${PATH}"

if [ -f "uv.lock" ]; then
    uv sync --locked --extra dev --no-install-project || uv pip install --python /opt/venv/bin/python pytest psutil maturin==1.14.1 fastapi uvicorn blake3 pyyaml jsonschema
else
    uv pip install --python /opt/venv/bin/python pytest psutil maturin==1.14.1 fastapi uvicorn blake3 pyyaml jsonschema
fi
uv pip install --python /opt/venv/bin/python maturin==1.14.1 pytest psutil || true
uv pip install --python /opt/venv/bin/python -e . --no-build-isolation || true

echo "    Verified Python runtime: $(python --version)"
echo "    Verified Rust runtime:   $(rustc --version)"

# 6. Enable cgroup v2 controllers
echo "[+] Step 6: Checking cgroup v2 configuration..."
if [ -d "/sys/fs/cgroup" ]; then
    echo "+cpu +memory +pids" > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true
fi

# ------------------------------------------------------------------------------
# 7. Execute Verification Gates
# ------------------------------------------------------------------------------
echo "================================================================================"
echo "  Executing Cloud Verification Gates"
echo "================================================================================"

# --- NV-001: Linux cgroup v2 live enforcement ---
echo "[+] [NV-001] Running Linux cgroup v2 live probe..."
NV001_RECEIPT="${EVIDENCE_DIR}/linux_cgroup_live_receipt.json"
systemctl enable --now docker || true
docker info --format '{{json .}}' > "${EVIDENCE_DIR}/docker_daemon_info.json" 2>/dev/null || true

cargo test --locked -p aegis-nerve --test linux_cgroup_native_probe -- --ignored > /tmp/cgroup_native.log 2>&1 || true

echo "[+] [P4-L1-L5] Running aegis-nerve Rust library unit & property tests..."
if cargo test --locked -p aegis-nerve --lib --no-default-features > "${EVIDENCE_DIR}/rust_lib_tests.log" 2>&1; then
    RUST_LIB_STATUS="PASS"
    RUST_LIB_VERIFIED=true
else
    RUST_LIB_STATUS="FAILED"
    RUST_LIB_VERIFIED=false
fi
cat <<EOF > "${EVIDENCE_DIR}/rust_lib_test_receipt.json"
{
  "schema": "aegis-rust-lib-test-receipt-v1",
  "truth_claim": false,
  "status": "${RUST_LIB_STATUS}",
  "verified": ${RUST_LIB_VERIFIED},
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF

if [ -f "scripts/linux_cgroup_live_probe.py" ]; then
    if ! python scripts/linux_cgroup_live_probe.py --output "${NV001_RECEIPT}"; then
        cat <<EOF > "${NV001_RECEIPT}"
{
  "schema": "aegis-linux-cgroup-v2-live-probe-v1",
  "truth_claim": false,
  "status": "FAILED",
  "verified": false,
  "error": "linux_cgroup_live_probe.py failed",
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
    fi
    if [ -f "${NV001_RECEIPT}" ]; then
        python -c "import json; p='${NV001_RECEIPT}'; d = json.load(open(p)); d['truth_claim'] = False; d['verified'] = d.get('status') == 'LIVE VERIFIED'; json.dump(d, open(p, 'w'), indent=2)" || true
    fi
fi

# --- NV-005: Hardware Policy Freeze (H1/H2) ---
echo "[+] [NV-005] Running Hardware Policy Benchmark for ${TIER}..."
if [ -f "scripts/resource_policy_benchmark.py" ]; then
    UPPER_TIER=$(echo "${TIER}" | tr '[:lower:]' '[:upper:]')
    export AEGIS_MACHINE_TYPE="${TIER}"
    if ! python scripts/resource_policy_benchmark.py \
        --profile "${UPPER_TIER}" \
        --proven \
        --output "${EVIDENCE_DIR}/resource_policy_${UPPER_TIER}_receipt.json" \
        --summary "${EVIDENCE_DIR}/resource_policy_${UPPER_TIER}_summary.md"; then
        cat <<EOF > "${EVIDENCE_DIR}/resource_policy_${UPPER_TIER}_receipt.json"
{
  "schema": "aegis-resource-policy-benchmark-v1",
  "truth_claim": false,
  "profile": "${UPPER_TIER}",
  "status": "FAILED",
  "verified": false,
  "error": "resource_policy_benchmark failed for ${UPPER_TIER}",
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
    fi

    if [ "${UPPER_TIER}" = "H2" ]; then
        if ! python scripts/resource_policy_benchmark.py \
            --profile "H1" \
            --proven \
            --output "${EVIDENCE_DIR}/resource_policy_H1_receipt.json" \
            --summary "${EVIDENCE_DIR}/resource_policy_H1_summary.md"; then
            cat <<EOF > "${EVIDENCE_DIR}/resource_policy_H1_receipt.json"
{
  "schema": "aegis-resource-policy-benchmark-v1",
  "truth_claim": false,
  "profile": "H1",
  "status": "FAILED",
  "verified": false,
  "error": "resource_policy_benchmark failed for H1",
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
        fi
    fi
fi

# --- NV-007: Fuzz Campaign Evidence ---
echo "[+] [NV-007] Running cargo fuzz campaign..."
FUZZ_RECEIPT="${EVIDENCE_DIR}/fuzz_campaign_receipt.json"
if [ -d "fuzz" ]; then
    cd fuzz
    fuzz_targets=("resource_contract" "protocol_frame" "archive_prefix" "runtime_ffi_contract")
    fuzz_failures=0
    total_crashes=0
    fuzz_extra_args=("-max_total_time=${FUZZ_MAX_TOTAL_TIME}")
    if [ "${FUZZ_RUNS}" -gt 0 ] 2>/dev/null; then
        fuzz_extra_args+=("-runs=${FUZZ_RUNS}")
    fi
    for target in "${fuzz_targets[@]}"; do
        echo "    Running fuzz target: ${target} (${fuzz_extra_args[*]})..."
        if ! cargo +nightly fuzz run "${target}" -- "${fuzz_extra_args[@]}" > "/tmp/fuzz_${target}.log" 2>&1; then
            echo "    [!] Warning: fuzz target ${target} returned non-zero exit code"
            fuzz_failures=$((fuzz_failures + 1))
        fi
        crashes=$(find . -maxdepth 2 -name "crash-*" 2>/dev/null | wc -l)
        total_crashes=$((total_crashes + crashes))
    done
    cd "${WORKSPACE}"
    if [ -f "scripts/sanitize_fuzz_logs.py" ]; then
        python scripts/sanitize_fuzz_logs.py || true
    fi

    if [ "${total_crashes}" -eq 0 ] && [ "${fuzz_failures}" -eq 0 ]; then
        FUZZ_STATUS="PASS"
        FUZZ_VERIFIED=true
    else
        FUZZ_STATUS="FAILED"
        FUZZ_VERIFIED=false
    fi

    cat <<EOF > "${FUZZ_RECEIPT}"
{
  "schema": "aegis-fuzz-campaign-receipt-v1",
  "truth_claim": false,
  "status": "${FUZZ_STATUS}",
  "verified": ${FUZZ_VERIFIED},
  "duration_seconds_per_target": ${FUZZ_MAX_TOTAL_TIME},
  "max_runs_cap": ${FUZZ_RUNS},
  "targets_exercised": ["resource_contract", "protocol_frame", "archive_prefix", "runtime_ffi_contract"],
  "crashes_discovered": ${total_crashes},
  "fuzz_failures": ${fuzz_failures},
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "platform": "Linux $(uname -r) $(uname -m) Cloud VM",
  "toolchain": "$(rustc --version 2>/dev/null || echo 'unknown')"
}
EOF
else
    cat <<EOF > "${FUZZ_RECEIPT}"
{
  "schema": "aegis-fuzz-campaign-receipt-v1",
  "truth_claim": false,
  "status": "FAILED",
  "verified": false,
  "crashes_discovered": 0,
  "error": "fuzz directory missing",
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
fi

# --- NV-008-L: Linux Wheel Parity (CPython 3.14.7) ---
echo "[+] [NV-008-L] Building and verifying Linux native wheel..."
WHEEL_RECEIPT="${EVIDENCE_DIR}/wheel_parity_linux_receipt.json"
mkdir -p /tmp/dist
maturin build --release -o /tmp/dist --interpreter /opt/venv/bin/python > /tmp/maturin_build.log 2>&1 || true
WHEEL_FILE=$(find /tmp/dist -name "*.whl" 2>/dev/null | head -n 1)
if [ -n "${WHEEL_FILE}" ]; then
    echo "    Found wheel: ${WHEEL_FILE}"
    rm -rf /tmp/wheel_test_venv
    uv venv --python 3.14.7 /tmp/wheel_test_venv
    uv pip install --python /tmp/wheel_test_venv/bin/python blake3 "${WHEEL_FILE}" || true
    if AEGIS_DESKTOP_SIDECAR=1 /tmp/wheel_test_venv/bin/python -c "import aegis_cognition; from aegis_cognition import aegis_nerve; print('Wheel and ABI loaded successfully:', dir(aegis_nerve))"; then
        cat <<EOF > "${WHEEL_RECEIPT}"
{
  "schema": "aegis-wheel-parity-receipt-v1",
  "truth_claim": false,
  "platform": "Linux $(uname -m) (glibc)",
  "wheel_filename": "$(basename "${WHEEL_FILE}")",
  "wheel_sha256": "$(sha256sum "${WHEEL_FILE}" | awk '{print $1}')",
  "verified": true,
  "status": "PROVEN",
  "python_version": "$(/tmp/wheel_test_venv/bin/python --version)",
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
    else
        cat <<EOF > "${WHEEL_RECEIPT}"
{
  "schema": "aegis-wheel-parity-receipt-v1",
  "truth_claim": false,
  "platform": "Linux $(uname -m) (glibc)",
  "wheel_filename": "$(basename "${WHEEL_FILE}")",
  "verified": false,
  "status": "FAILED",
  "error": "Failed importing aegis_cognition or aegis_nerve ABI from installed wheel",
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
    fi
else
    cat <<EOF > "${WHEEL_RECEIPT}"
{
  "schema": "aegis-wheel-parity-receipt-v1",
  "truth_claim": false,
  "platform": "Linux $(uname -m)",
  "verified": false,
  "status": "FAILED",
  "error": "No wheel artifact produced by maturin",
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
fi

# --- NV-013: Miri & ASan Closure ---
echo "[+] [NV-013] Running Miri and AddressSanitizer evidence..."
MIRI_ASAN_RECEIPT="${EVIDENCE_DIR}/miri_asan_receipt.json"
MIRI_STATUS="PASS"
ASAN_STATUS="PASS"
(MIRIFLAGS="-Zmiri-disable-isolation" cargo +nightly miri test --locked -p aegis-nerve --test resource_contract_matrix --no-default-features > /tmp/miri.log 2>&1) || MIRI_STATUS="FAIL"
(RUSTFLAGS="-Z sanitizer=address" cargo +nightly test --locked -p aegis-nerve --test resource_contract_matrix --target "$(uname -m)-unknown-linux-gnu" -Zbuild-std --no-default-features > /tmp/asan.log 2>&1) || ASAN_STATUS="FAIL"

OVERALL_STATUS="PASS"
if [ "${MIRI_STATUS}" != "PASS" ] || [ "${ASAN_STATUS}" != "PASS" ]; then
    OVERALL_STATUS="FAILED"
fi

cat <<EOF > "${MIRI_ASAN_RECEIPT}"
{
  "schema": "aegis-miri-asan-evidence-receipt-v1",
  "truth_claim": false,
  "status": "${OVERALL_STATUS}",
  "verified": $([ "${OVERALL_STATUS}" = "PASS" ] && echo true || echo false),
  "miri_status": "${MIRI_STATUS}",
  "asan_status": "${ASAN_STATUS}",
  "tested_module": "core/rust/tests/resource_contract_matrix.rs",
  "platform": "Linux $(uname -m) Cloud VM",
  "toolchain": "$(rustc +nightly --version 2>/dev/null || echo 'unknown')",
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF

# --- NV-014: Communication Payload Benchmark ---
echo "[+] [NV-014] Running communication payload benchmark..."
NV014_RECEIPT="${EVIDENCE_DIR}/communication_payload_receipt.json"
if [ -f "scripts/communication_payload_benchmark.py" ]; then
    if ! python scripts/communication_payload_benchmark.py --output "${NV014_RECEIPT}"; then
        cat <<EOF > "${NV014_RECEIPT}"
{
  "schema": "aegis-communication-payload-benchmark-v1",
  "truth_claim": false,
  "status": "FAILED",
  "verified": false,
  "error": "communication_payload_benchmark.py returned non-zero",
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
    fi
fi

# --- NV-017: QuickJS Cold Start Gate ---
echo "[+] [NV-017] Running QuickJS cold start gate..."
NV017_RECEIPT="${EVIDENCE_DIR}/quickjs_full_interpreter_cold_start_capture.json"
if [ -f "scripts/quickjs_cold_start_gate.py" ]; then
    if [ -f "artifacts/quickjs_full_interpreter.wasm" ]; then
        if ! command -v wasmtime &>/dev/null; then
            curl https://wasmtime.dev/install.sh -sSf | bash || true
            export PATH="/root/.wasmtime/bin:${PATH}"
            ln -sf /root/.wasmtime/bin/wasmtime /usr/local/bin/wasmtime 2>/dev/null || true
        fi
        if command -v wasmtime &>/dev/null; then
            cat << 'EOF_QJS' > /usr/local/bin/qjs-wasm-runner
#!/usr/bin/env bash
set -euo pipefail
exec wasmtime run "$1" "$(cat "$2")"
EOF_QJS
            chmod +x /usr/local/bin/qjs-wasm-runner
            export AEGIS_QUICKJS_WASM_PATH="${WORKSPACE}/artifacts/quickjs_full_interpreter.wasm"
            export AEGIS_QUICKJS_FULL_INTERPRETER_RUNNER_JSON='["/usr/local/bin/qjs-wasm-runner", "{runtime}", "{script}"]'
        fi
    fi
    python scripts/quickjs_cold_start_gate.py --write-capture || true
    python scripts/quickjs_cold_start_gate.py || true
    if [ -f "artifacts/quickjs_full_interpreter_cold_start_capture.json" ]; then
        cp artifacts/quickjs_full_interpreter_cold_start_capture.json "${EVIDENCE_DIR}/"
    fi
    if [ -f "artifacts/quickjs_cold_start_gate_report.json" ]; then
        cp artifacts/quickjs_cold_start_gate_report.json "${EVIDENCE_DIR}/"
    fi
fi
if [ ! -f "${NV017_RECEIPT}" ]; then
    cat <<EOF > "${NV017_RECEIPT}"
{
  "schema": "aegis-quickjs-full-interpreter-cold-start-capture-v1",
  "truth_claim": false,
  "status": "FAILED",
  "verified": false,
  "error": "Failed generating quickjs cold start capture",
  "verified_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
fi

# --- NV-020: Trust Policy Owner & Cross-Cell Binding ---
echo "[+] [NV-020] Verifying trust policy hash across constructor matrix..."
TRUST_RECEIPT="${EVIDENCE_DIR}/trust_policy_hash_receipt.json"
export AEGIS_DESKTOP_SIDECAR=1
export PYTHONPATH="${WORKSPACE}:${PYTHONPATH:-}"
python -c "
import json
from datetime import UTC, datetime

try:
    from core.python.aegis.trust_policy import trust_policy_hash, trust_policy_payload
    from aegis_cognition.config import trust_policy_hash as config_hash

    matrix = {}
    all_match = True
    for level in ('DEV', 'STAGING', 'PROD'):
        ch = config_hash(level)
        th = trust_policy_hash(level)
        payload = trust_policy_payload(level)
        match = (ch == th)
        if not match:
            all_match = False
        matrix[level] = {
            'config_hash': ch,
            'canonical_hash': th,
            'hash_match': match,
            'missing_artifact_policy': payload.get('missing_artifact_policy'),
        }

    receipt = {
        'schema': 'aegis-trust-policy-cross-cell-receipt-v1',
        'truth_claim': False,
        'verified': all_match,
        'status': 'PROVEN' if all_match else 'FAILED',
        'matrix': matrix,
        'verified_at_utc': datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
    }
except Exception as err:
    receipt = {
        'schema': 'aegis-trust-policy-cross-cell-receipt-v1',
        'truth_claim': False,
        'verified': False,
        'status': 'FAILED',
        'error': f'Import or execution error: {err}',
        'matrix': {},
        'verified_at_utc': datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
    }

with open('${TRUST_RECEIPT}', 'w', encoding='utf-8') as f:
    json.dump(receipt, f, indent=2)
print('NV-020 verification complete: status =', receipt['status'])
"

# Sync sccache back to GCS if enabled
if [ -n "${SCCACHE_PREFIX}" ] && [ -d "/opt/sccache" ]; then
    gcloud storage rsync /opt/sccache "${SCCACHE_PREFIX}" --quiet >/dev/null 2>&1 || true
fi

# Generate PROVENANCE.json for evidence dir
python -c "
import json
from pathlib import Path
from scripts.cloud_m8.provenance import build_provenance_manifest
manifest = build_provenance_manifest(
    Path('${EVIDENCE_DIR}'),
    root=Path('${WORKSPACE}'),
    tier='gcp-${ZONE}-${TIER}',
    exact_command='startup_compute.sh (run_id=${RUN_ID})',
)
Path('${EVIDENCE_DIR}/PROVENANCE.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
" || true

# ------------------------------------------------------------------------------
# 8. Package and Upload Evidence to Unique Run Prefix
# ------------------------------------------------------------------------------
echo "================================================================================"
echo "  Packaging Evidence Receipts"
echo "================================================================================"

EVIDENCE_ARCHIVE="/tmp/evidence-${VM_NAME}.tar.gz"
tar -czf "${EVIDENCE_ARCHIVE}" -C "${EVIDENCE_DIR}" .

if [ -n "${GCS_BUCKET}" ]; then
    echo "[+] Uploading evidence to ${GCS_RUN_URI}/evidence-${VM_NAME}.tar.gz..."
    gcloud storage cp "${EVIDENCE_ARCHIVE}" "${GCS_RUN_URI}/evidence-${VM_NAME}.tar.gz"

    echo "[+] Writing completion sentinel to ${GCS_RUN_URI}/DONE-${VM_NAME}..."
    echo "DONE $(date -u +"%Y-%m-%dT%H:%M:%SZ")" > /tmp/DONE
    gcloud storage cp /tmp/DONE "${GCS_RUN_URI}/DONE-${VM_NAME}"
fi

echo "[+] Startup execution finished successfully at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
exit 0
