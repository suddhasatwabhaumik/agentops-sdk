#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

set -euo pipefail

echo "Starting E2E Tests for Gateway Security & Capacity Remediation (v1)..."
echo "========================================================"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "$DIR/test_1_jwt_enforcement.sh"
echo ""
bash "$DIR/test_2_telemetry_batching.sh"
echo ""

echo "========================================================"
echo "[PASS] All E2E Gateway Security & Capacity tests completed successfully."
