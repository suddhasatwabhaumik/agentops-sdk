#!/usr/bin/env bash
# Starts or stops the local observability stack (Alloy, OTel Collector, Prometheus, Tempo, Loki, Grafana).
# The stack itself lives in examples/full_stack; this script only adds the tutorial port overrides.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="$HERE/../../full_stack/docker-compose.yaml"
compose() { docker compose -p agentops-tutorial -f "$BASE" -f "$HERE/compose.ports.yaml" "$@"; }
case "${1:-}" in
  up)     compose up -d && echo "Grafana: http://localhost:3002  Prometheus: http://localhost:9090  Tempo: http://localhost:3200  OTLP: localhost:4317" ;;
  down)   compose down ;;
  purge)  compose down -v ;;
  status) compose ps ;;
  logs)   compose logs -f "${2:-}" ;;
  *)      echo "usage: $0 up|down|purge|status|logs [service]"; exit 1 ;;
esac
