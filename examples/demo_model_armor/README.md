<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Model Armor Safety Demo (`v1.0.0`)

Demonstrates fail-closed inbound and outbound content safety using `SecurityGuard`, `LocalRuleSafetyScanner`, and `CloudModelArmorScanner` (`SecurityVerdict.ALLOW`, `BLOCK`, and `SANITIZED`).

## How to Run

### Offline Mode (No GCP Credentials Required)
Uses `LocalRuleSafetyScanner` with `SecurityGuard(scanner=..., fail_closed=True)`:

```bash
python examples/demo_model_armor/demo_model_armor_safety.py --mode offline
```

### Live Google Cloud Model Armor Mode
Set your GCP project (`MODEL_ARMOR_PROJECT_ID` or `GOOGLE_CLOUD_PROJECT`), region (`MODEL_ARMOR_LOCATION` or `GOOGLE_CLOUD_LOCATION`, defaulting to `europe-west4`), and pre-provisioned Model Armor template IDs (`MODEL_ARMOR_INPUT_TEMPLATE` or `MODEL_ARMOR_INPUT_TEMPLATE_ID`, plus optional `MODEL_ARMOR_OUTPUT_TEMPLATE` or `MODEL_ARMOR_OUTPUT_TEMPLATE_ID`), then run with `--mode gcp`:

```bash
export MODEL_ARMOR_PROJECT_ID="<your-gcp-project-id>"
export MODEL_ARMOR_LOCATION="europe-west4"
export MODEL_ARMOR_INPUT_TEMPLATE_ID="agentops-input-guard"
export MODEL_ARMOR_OUTPUT_TEMPLATE_ID="agentops-output-guard"

python examples/demo_model_armor/demo_model_armor_safety.py --mode gcp
```

## Features Demonstrated
1. **Inbound Prompt Protection (`guard.check_input`)**: Blocks prompt injection and jailbreak attempts with `SecurityVerdict.BLOCK`.
2. **Outbound PII Redaction (`guard.check_output`)**: Returns `SecurityVerdict.SANITIZED` with emails and phone numbers masked cleanly before reaching the user.
3. **Fail-Closed Enforcement (`fail_closed=True`)**: Ensures unavailable scanners or transport errors never fail silently open.
