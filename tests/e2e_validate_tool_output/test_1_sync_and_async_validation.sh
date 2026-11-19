#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 1: Sync & Async Tool Output Schema Validation (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

"${PYTHON_BIN}" - << 'PYEOF'
import asyncio
import json
from pydantic import BaseModel
from pydantic import Field

from agentops_sdk import ToolValidationError
from agentops_sdk import validate_tool_output


class PredictionOutput(BaseModel):
    part_id: str
    predicted_demand: int = Field(..., ge=0)
    confidence_score: float = Field(..., ge=0.0, le=1.0)


@validate_tool_output(schema_model=PredictionOutput)
def sync_dict_tool():
    return {"part_id": "PART-001", "predicted_demand": 45, "confidence_score": 0.92}


@validate_tool_output(schema_model=PredictionOutput)
def sync_model_tool():
    return PredictionOutput(part_id="PART-002", predicted_demand=10, confidence_score=0.88)


@validate_tool_output(schema_model=PredictionOutput)
def sync_list_tool():
    return [
        {"part_id": "PART-003", "predicted_demand": 12, "confidence_score": 0.95},
        {"part_id": "PART-004", "predicted_demand": 8, "confidence_score": 0.81},
    ]


@validate_tool_output(schema_model=PredictionOutput)
def sync_tuple_tool():
    return (
        {"part_id": "PART-005", "predicted_demand": 99, "confidence_score": 0.99},
        {"part_id": "PART-006", "predicted_demand": 3, "confidence_score": 0.70},
    )


@validate_tool_output(schema_model=PredictionOutput)
def sync_json_str_tool():
    return json.dumps({"part_id": "PART-007", "predicted_demand": 50, "confidence_score": 0.85})


assert sync_dict_tool()["part_id"] == "PART-001"
assert sync_model_tool().part_id == "PART-002"
assert len(sync_list_tool()) == 2
assert isinstance(sync_tuple_tool(), tuple) and len(sync_tuple_tool()) == 2
assert json.loads(sync_json_str_tool())["part_id"] == "PART-007"


async def run_async_tests():
    @validate_tool_output(schema_model=PredictionOutput)
    async def async_dict_tool():
        await asyncio.sleep(0.01)
        return {"part_id": "ASYNC-001", "predicted_demand": 100, "confidence_score": 0.94}

    @validate_tool_output(schema_model=PredictionOutput)
    async def async_tuple_tool():
        await asyncio.sleep(0.01)
        return (
            {"part_id": "ASYNC-002", "predicted_demand": 22, "confidence_score": 0.87},
            {"part_id": "ASYNC-003", "predicted_demand": 33, "confidence_score": 0.91},
        )

    @validate_tool_output(schema_model=PredictionOutput)
    async def async_json_tool():
        await asyncio.sleep(0.01)
        return json.dumps({"part_id": "ASYNC-004", "predicted_demand": 75, "confidence_score": 0.89})

    assert (await async_dict_tool())["part_id"] == "ASYNC-001"
    res_t = await async_tuple_tool()
    assert isinstance(res_t, tuple) and len(res_t) == 2
    assert "ASYNC-004" in (await async_json_tool())


asyncio.run(run_async_tests())


@validate_tool_output(schema_model=PredictionOutput, max_retries=2)
def invalid_schema_tool():
    return {"part_id": "BAD", "predicted_demand": -5, "confidence_score": 2.5}


try:
    invalid_schema_tool()
    raise AssertionError("invalid_schema_tool should have raised ToolValidationError")
except ToolValidationError as exc:
    assert exc.attempts == 2
    assert exc.tool_name == "invalid_schema_tool"

print("[PASS] Test 1: Sync & Async tool output validation verified.")
PYEOF
