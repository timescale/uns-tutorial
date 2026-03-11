#!/usr/bin/env bash
# =============================================================================
# run-all.sh — Run all four edge-case experiments in sequence
# Tutorial: Ask Your Factory Floor Anything
# =============================================================================
#
# Usage:
#   bash run-all.sh
#
# Prerequisites:
#   docker compose up -d
#   uv sync
# =============================================================================

set -euo pipefail

PASS=0
FAIL=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
check_db() {
    if ! docker compose exec -T timescaledb pg_isready -U postgres -d uns_demo -q 2>/dev/null; then
        echo "ERROR: TimescaleDB is not ready. Run: docker compose up -d"
        exit 1
    fi
}

run_experiment() {
    local name="$1"
    local script="$2"
    echo ""
    echo "======================================================================"
    echo "  RUNNING: $name"
    echo "======================================================================"
    if uv run "$script"; then
        PASS=$((PASS + 1))
        return 0
    else
        FAIL=$((FAIL + 1))
        echo "  [FAILED] $name exited with error"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Pre-flight check
# ---------------------------------------------------------------------------
echo "======================================================================"
echo "UNS GOVERNANCE DEMO — FULL SUITE"
echo "======================================================================"
echo ""
echo "Checking database connection..."
check_db
echo "✓ Database ready"

# ---------------------------------------------------------------------------
# Run experiments (edge-cases only — no MQTT broker required)
# ---------------------------------------------------------------------------
run_experiment "Edge Case 1: Timezone Drift"           "src/edge-cases/timezone-drift.py"
run_experiment "Edge Case 2: Duplicate Events"         "src/edge-cases/duplicate-events.py"
run_experiment "Edge Case 3: Missing Fields"           "src/edge-cases/missing-fields.py"
run_experiment "Edge Case 4: Non-Standard Enums"       "src/edge-cases/non-standard-enums.py"

# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo "UNS GOVERNANCE DEMO — SUMMARY"
echo "======================================================================"
echo ""
printf "  %-30s  %-28s  %-28s\n" "Edge Case" "Without Governance" "With Governance"
echo "  -----------------------------------------------------------------------"
printf "  %-30s  %-28s  %-28s\n" "1. Timezone Drift"       "Silent wrong time buckets" "Rejected at ingestion"
printf "  %-30s  %-28s  %-28s\n" "2. Duplicate Events"     "130 rows, inflated count"  "100 rows, accurate count"
printf "  %-30s  %-28s  %-28s\n" "3. Missing Fields"       "Silent NULLs in results"   "Rejected + dead_letter"
printf "  %-30s  %-28s  %-28s\n" "4. Non-Standard Enums"   "Invisible to queries"      "Rejected + suggested fix"
echo ""
echo "  Results: $PASS passed, $FAIL failed"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "  Some experiments failed. Check output above for details."
    exit 1
fi

echo "  All experiments complete."
echo ""
echo "  Next steps:"
echo "    # Run the MQTT wildcard demo (requires broker):"
echo "    uv run src/wildcard-demo.py"
echo ""
echo "    # Run queries against the seed data:"
echo "    psql \$TIMESCALE_DSN -f queries/uns-metadata-lookup.sql"
echo "    psql \$TIMESCALE_DSN -f queries/functional-namespace.sql"
echo "    psql \$TIMESCALE_DSN -f queries/cross-domain-join.sql"
echo "    psql \$TIMESCALE_DSN -f queries/governance-audit.sql"
echo ""
