# 10 · Privacy with Cloud DLP: No Personal Data Leaves the Agent (`v1.0.0`)

**What you learn:** how to redact names, e-mails, and phone numbers from generated text using regional Google Cloud Sensitive Data Protection (`GoogleDLPScrubber` + `OutputDLPSanitizer`), and verify redaction on the OpenTelemetry span.

```
Gemini draft ──► await OutputDLPSanitizer(scrubber=GoogleDLPScrubber(project_id)).scrub_text(raw)
                      │ Regional DLP deidentifyContent (dlp.<region>.rep.googleapis.com)
                      │ info types: EMAIL_ADDRESS, PHONE_NUMBER, PERSON_NAME, CREDIT_CARD_NUMBER, ...
                      ▼
                 Redacted text + active span attribute agentops.privacy.pii_scrubbed = True
```

## SDK calls used

| Call | What it does |
|---|---|
| `GoogleDLPScrubber(project_id, location=..., info_types=...)` | Calls Cloud DLP `deidentifyContent` on the regional GCP endpoint (raises `PrivacyScrubError` on failure; zero silent fallback) |
| `OutputDLPSanitizer(scrubber=scrubber)` | Async/sync sanitizer exposing `await scrub_text(text)` and `scrub(text)`, stamping `agentops.privacy.pii_scrubbed` onto the active OTel span |
| `RegexPrivacyScrubber` | Deterministic offline `PrivacyScrubberPort` substitute for local unit tests and demos |

Passing `GoogleDLPScrubber` explicitly ensures DLP API or IAM failures surface immediately instead of silently degrading.

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| API `dlp.googleapis.com` | Cloud Sensitive Data Protection | `roles/serviceusage.serviceUsageAdmin` once to enable (`00_setup/provision.py apis`) |
| Cloud DLP `deidentifyContent` | Runtime PII redaction | `roles/dlp.user` |
| Vertex AI, Gemini | Generates the sample dealer draft | `roles/aiplatform.user` |

## Run

```bash
python examples/tutorial/10_privacy_dlp/main.py
```

Expected: prints the raw dealer draft containing synthetic contact PII, the DLP-scrubbed output, and `span agentops.privacy.pii_scrubbed=True`.
