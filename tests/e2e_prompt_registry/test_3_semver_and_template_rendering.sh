#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 3: SemVer Version Management & Sandboxed Jinja2 Rendering (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"
export REPO_ROOT="${REPO_ROOT}"

"${PYTHON_BIN}" - << 'PYEOF'
import os
from pathlib import Path

from agentops_sdk import LocalYAMLPromptAdapter
from agentops_sdk import PromptRegistry

repo_root = Path(os.environ["REPO_ROOT"])
prompts_dir = repo_root / "tests" / "e2e_prompt_registry" / "prompt_examples"
registry = PromptRegistry(
    backend="local", adapter=LocalYAMLPromptAdapter(local_dir=str(prompts_dir))
)

complex_template = """
Agent Report for Mission: {{ mission_name | upper }}
Status: {% if active %}OPERATIONAL{% else %}DECOMMISSIONED{% endif %}
Assigned Units:
{% for unit in units %}
  - Unit {{ unit.id }}: {{ unit.role }} (Level: {{ unit.tier }})
{% endfor %}
Total Units: {{ units | length }}
"""

formatted = registry.format_prompt(
    complex_template,
    {
        "mission_name": "Enterprise Precision Ops",
        "active": True,
        "units": [
            {"id": "TR-100", "role": "Tractor Guidance", "tier": "A"},
            {"id": "SP-200", "role": "Sprayer Sensor", "tier": "B"},
            {"id": "HV-300", "role": "Combine Harvester", "tier": "A"},
        ],
    },
)
assert "ENTERPRISE PRECISION OPS" in formatted
assert "Status: OPERATIONAL" in formatted
assert "Unit TR-100: Tractor Guidance" in formatted
assert "Total Units: 3" in formatted

default_sql = registry.render_prompt(
    "sql_agent_prompt",
    {
        "dialect": "PostgreSQL",
        "schema_context": "TABLE telemetry (id INT, latency FLOAT)",
        "user_query": "SELECT AVG(latency) FROM telemetry;",
    },
)
assert "PostgreSQL" in default_sql and "SELECT AVG(latency)" in default_sql

sales = registry.render_prompt(
    "sales_advisor_prompt",
    {
        "customer_name": "Acme Corp",
        "product_line": "Series-T Equipment",
        "budget": "$350,000",
    },
)
assert "Acme Corp" in sales and "Series-T Equipment" in sales
print("[PASS] Test 3: Jinja2 rendering and prompt examples verified.")
PYEOF
