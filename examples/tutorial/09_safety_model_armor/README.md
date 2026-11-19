# 09 · Safety with Model Armor: Block Injections In, Sanitize PII Out (`v1.0.0`)

**What you learn:** how to enforce fail-closed Google Cloud Model Armor checks before and after Gemini inference using `CloudModelArmorScanner` and `SecurityGuard`, verifying that real cloud templates are called.

```
user prompt ──► guard.check_input() ──► CloudModelArmorScanner :sanitizeUserPrompt (agentops-input-guard)
                   │ BLOCK (prompt injection, jailbreak, malicious URL, or error when fail_closed=True)
                   │       ──► stop, span safety.verdict=BLOCK
                   ▼ ALLOW
               Gemini ──► draft ──► guard.check_output() ──► CloudModelArmorScanner :sanitizeModelResponse (agentops-output-guard)
                                                                  └─ SANITIZED (SDP redacts PII cleanly)
```

## SDK calls used

| Call | What it does |
|---|---|
| `CloudModelArmorScanner(project_id, location, input_template_id, output_template_id)` | Calls Cloud Model Armor REST v1 endpoints (`:sanitizeUserPrompt`, `:sanitizeModelResponse`) |
| `SecurityGuard(scanner=scanner, fail_closed=True)` | Wraps a `SafetyScannerPort` with fail-closed policy enforcement (`check_input`, `check_output`) |
| `SecurityGuard.check_input(text)` / `.check_output(text)` | Returns `DecisionResult(allowed, verdict, sanitized_text, reason, violation_category, latency_ms)` |
| `SecurityVerdict` | `ALLOW`, `BLOCK`, or `SANITIZED` |
| `LocalRuleSafetyScanner` | Deterministic offline `SafetyScannerPort` test double (explicit opt-in only) |

**Fail-closed default:** `SecurityGuard` and `CloudModelArmorScanner` default to `fail_closed=True` (`MODEL_ARMOR_FAIL_CLOSED=true`). Network, auth, or missing-template errors always return `SecurityVerdict.BLOCK` (`allowed=False`) rather than silently falling back to regex rules. Security counters record to `agentops.security.checks.total`, `agentops.security.violations.total`, and `agentops.security.check.latency`.

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| Model Armor templates `agentops-input-guard` and `agentops-output-guard` in `europe-west4` (provisioned via central infrastructure IaC) | Input & output guardrails | `roles/modelarmor.user` and `roles/modelarmor.viewer` |
| API `modelarmor.googleapis.com` | Model Armor service endpoint | Enabled in project |

## Run

```bash
python examples/tutorial/09_safety_model_armor/main.py
```

Expected: benign prompt receives `ALLOW` and a Gemini answer; prompt injection receives `BLOCK` and `[blocked before the model]`; outbound response containing PII receives `SANITIZED` with sensitive entities redacted. Check Prometheus metric `agentops_security_checks_total{verdict="BLOCK"}` for your `run_id`.
