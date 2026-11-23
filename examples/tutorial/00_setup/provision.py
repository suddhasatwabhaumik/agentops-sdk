"""Creates the GCP resources the tutorials use. Run once per project.

Everything is idempotent and nothing is torn down afterwards (the resources
are meant to stay for the team).

Run from the repository root:
    python examples/tutorial/00_setup/provision.py <resource> [<resource> ...]
    python examples/tutorial/00_setup/provision.py all

Resources: apis, bigquery, model-armor, agent-engine, cloudsql
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import settings

cfg = settings.load("00_provision")

from resources import agent_engine
from resources import apis
from resources import bigquery_table
from resources import cloudsql
from resources import model_armor

RESOURCES = {
    "apis": (
        apis,
        "enable dlp.googleapis.com and sqladmin.googleapis.com",
        "roles/serviceusage.serviceUsageAdmin",
    ),
    "bigquery": (
        bigquery_table,
        "dataset agentops_tutorial + table tenant_inventory",
        "roles/bigquery.dataEditor + jobUser (or bigquery.admin)",
    ),
    "model-armor": (
        model_armor,
        "Verify Model Armor templates agentops-input-guard / "
        "agentops-output-guard (provisioned via central infrastructure IaC)",
        "roles/modelarmor.viewer or roles/modelarmor.user",
    ),
    "agent-engine": (
        agent_engine,
        "Vertex AI Agent Engine for the Memory Bank",
        "roles/aiplatform.user",
    ),
    "cloudsql": (
        cloudsql,
        "Cloud SQL PostgreSQL instance agentops-tutorial (billed while up)",
        "roles/cloudsql.admin",
    ),
}


def main(names: list[str]) -> int:
    if not names or any(n not in RESOURCES and n != "all" for n in names):
        print(__doc__)
        return 2
    selected = list(RESOURCES) if "all" in names else names
    failures = 0
    for name in selected:
        module, what, role = RESOURCES[name]
        print(f"\n== {name}: {what}")
        try:
            print("  ", module.ensure(cfg.project, cfg.location))
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures += 1
            print(
                f"   FAILED: {str(exc)[:300]}\n"
                f"   needs: {role} (see examples/tutorial/ACCESS.md)"
            )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
