# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Multi-turn user scenario generation and agent conversation simulation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import time
from typing import Any

from google import genai
from google.genai import types as genai_types
import vertexai
from vertexai._genai.types import evals as eval_types

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import cloud_models


def normalize_agent_info(info: Any) -> Any:
    """Normalizes None, Mapping, or AgentInfo into vertexai AgentInfo."""
    if isinstance(info, eval_types.AgentInfo) and info.agents:
        return info
    if isinstance(info, eval_types.AgentInfo):
        raw = info.model_dump(exclude_none=True)
    elif isinstance(info, Mapping) or info is None:
        raw = dict(info or {})
    else:
        raise TypeError("agent_info must be AgentInfo or Mapping.")
    root_id = str(raw.get("root_agent_id") or "root")
    raw["root_agent_id"] = root_id
    raw.setdefault("name", "agent_under_test")
    raw_agents = raw.get("agents")
    if isinstance(raw_agents, Sequence) and not isinstance(raw_agents, str):
        raw["agents"] = {
            str(entry.get("agent_id", f"a_{idx}")): entry
            for idx, entry in enumerate(raw_agents)
            if isinstance(entry, Mapping)
        }
    raw["agents"] = raw.get("agents") or {root_id: {"agent_id": root_id}}
    return eval_types.AgentInfo.model_validate(raw)


def generate_conversation_scenarios(
    agent_info: Any,
    config: cloud_models.UserScenarioGenerationConfig,
    *,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> list[cloud_models.UserScenario]:
    """Generates user conversation scenarios via Vertex AI GenAI Evals."""
    proj, loc = settings.resolve_gcp_project_and_location(
        project_id, location, require_project=True
    )
    gen_kwargs = config.model_dump(
        exclude={"allow_cross_region_model"}, exclude_none=True
    )
    try:
        active_client = client or vertexai.Client(project=proj, location=loc)
        dataset = active_client.evals.generate_conversation_scenarios(
            agent_info=normalize_agent_info(agent_info),
            config=eval_types.UserScenarioGenerationConfig(**gen_kwargs),
            allow_cross_region_model=config.allow_cross_region_model,
        )
    except Exception as exc:
        raise exceptions.EvaluationError(
            f"Scenario generation failed: {exc}"
        ) from exc
    scenarios = cloud_models.UserScenario.from_eval_cases(
        dataset, config.environment_context
    )
    if not scenarios:
        raise exceptions.EvaluationError("No eval_cases returned.")
    return scenarios


def _resolve_agent_callable(agent: Any) -> Any:
    if callable(agent):
        return agent
    for attr in ("query", "run"):
        fn = getattr(agent, attr, None)
        if callable(fn):
            return fn
    raise TypeError("agent must be callable or implement .query() or .run().")


def _generate_next_user_turn(
    simulator: Any,
    config: cloud_models.UserSimulatorConfig,
    scenario: cloud_models.UserScenario,
    turns: list[dict[str, Any]],
) -> str:
    history = "\n".join(
        f"{turn['role'].upper()}: {turn['content']}" for turn in turns
    )
    instruction = config.simulation_instruction or "Simulate user."
    goal = scenario.goal or scenario.conversation_plan
    prompt = (
        f"Goal: {goal}\nInstruction: {instruction}\n"
        f"History:\n{history}\nNext user turn or [DONE]:"
    )
    gen_cfg = (
        genai_types.GenerateContentConfig(temperature=config.temperature)
        if config.temperature is not None
        else None
    )
    try:
        reply = simulator.models.generate_content(
            model=config.model_name, contents=prompt, config=gen_cfg
        )
    except Exception as exc:
        raise exceptions.EvaluationError(f"Simulator failed: {exc}") from exc
    return str(getattr(reply, "text", "") or "").strip()


def _invoke_agent_turn(
    agent_fn: Any, prompt: str, turns: list[dict[str, Any]]
) -> dict[str, Any]:
    try:
        reply = agent_fn(prompt, history=turns)
    except TypeError:
        reply = agent_fn(prompt)
    msg: dict[str, Any] = {"role": "agent", "content": str(reply)}
    if isinstance(reply, Mapping):
        msg["content"] = str(reply.get("content") or reply.get("response", ""))
        msg["tool_calls"] = reply.get("tool_calls")
    return msg


def _simulate_single_scenario(
    scenario: cloud_models.UserScenario,
    agent_fn: Any,
    simulator: Any,
    config: cloud_models.UserSimulatorConfig,
) -> cloud_models.SimulationTrajectory:
    start_time = time.monotonic()
    prompt = scenario.starting_prompt
    completed = False
    turns: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    for turn_idx in range(config.max_turns):
        turns.append(_invoke_agent_turn(agent_fn, prompt, turns))
        next_prompt = (
            _generate_next_user_turn(simulator, config, scenario, turns)
            if turn_idx + 1 < config.max_turns
            else ""
        )
        if (
            not next_prompt
            or "[DONE]" in next_prompt
            or "<STOP>" in next_prompt
        ):
            completed = True
            break
        prompt = next_prompt
        turns.append({"role": "user", "content": prompt})
    return cloud_models.SimulationTrajectory(
        scenario_id=scenario.scenario_id,
        trajectory=turns,
        completed=completed,
        turns_count=len(turns),
        duration_seconds=round(time.monotonic() - start_time, 3),
    )


def simulate_agent_conversations(
    scenarios: Sequence[cloud_models.UserScenario],
    agent: Any,
    simulator_config: cloud_models.UserSimulatorConfig | None = None,
    *,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> list[cloud_models.SimulationTrajectory]:
    """Executes multi-turn simulation using google.genai.Client."""
    agent_fn = _resolve_agent_callable(agent)
    if not scenarios:
        return []
    config = simulator_config or cloud_models.UserSimulatorConfig()
    if client is None:
        proj, loc = settings.resolve_gcp_project_and_location(
            project_id, location, require_project=True
        )
        simulator: Any = genai.Client(vertexai=True, project=proj, location=loc)
    else:
        simulator = client
    return [
        _simulate_single_scenario(scenario, agent_fn, simulator, config)
        for scenario in scenarios
    ]
