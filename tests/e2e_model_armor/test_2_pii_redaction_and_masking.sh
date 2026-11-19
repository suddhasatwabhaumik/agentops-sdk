#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# TEST 2: Outbound PII Redaction & SecurityVerdict.SANITIZED (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

OFFLINE=false
if [[ "${1:-}" == "--offline" ]]; then
    OFFLINE=true
    shift
fi
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-}}}"
LOCATION="${2:-${MODEL_ARMOR_LOCATION:-europe-west4}}"
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

"${PYTHON_BIN}" - << PYEOF
from agentops_sdk import CloudModelArmorScanner
from agentops_sdk import LocalRuleSafetyScanner
from agentops_sdk import SecurityGuard
from agentops_sdk import SecurityVerdict

offline = "${OFFLINE}" == "true" or not "${PROJECT_ID}"
if offline:
    scanner = LocalRuleSafetyScanner()
else:
    scanner = CloudModelArmorScanner(
        project_id="${PROJECT_ID}",
        location="${LOCATION}",
        input_template_id="agentops-input-guard",
        output_template_id="agentops-output-guard",
    )
guard = SecurityGuard(scanner=scanner, fail_closed=True)

clean = "Recommended hydraulic oil for Series-800 is OEM Fluid Plus."
dec_clean = guard.check_output(clean)
assert dec_clean.allowed is True
assert dec_clean.verdict == SecurityVerdict.ALLOW
assert dec_clean.sanitized_text == clean

email_resp = "Send invoice to john.doe@example.com for approval."
dec_email = guard.check_output(email_resp)
assert dec_email.allowed is True
assert dec_email.verdict == SecurityVerdict.SANITIZED
assert "john.doe@example.com" not in dec_email.sanitized_text
if offline:
    assert "[EMAIL_ADDRESS]" in dec_email.sanitized_text

phone_resp = "Emergency harvest dispatch: call (555) 234-5678 immediately."
dec_phone = guard.check_output(phone_resp)
assert dec_phone.allowed is True
assert dec_phone.verdict == SecurityVerdict.SANITIZED
assert "(555) 234-5678" not in dec_phone.sanitized_text
if offline:
    assert "[PHONE_NUMBER]" in dec_phone.sanitized_text

multi_resp = (
    "Order ORD-9921 placed by alice.smith@example.com. "
    "Call (800) 555-4321 or bob.ross@example.org."
)
dec_multi = guard.check_output(multi_resp)
assert dec_multi.verdict == SecurityVerdict.SANITIZED
assert "alice.smith@example.com" not in dec_multi.sanitized_text
assert "bob.ross@example.org" not in dec_multi.sanitized_text

print("[PASS] Test 2: Outbound PII redaction & SANITIZED policy verified.")
PYEOF
