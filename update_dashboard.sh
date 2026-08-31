#!/usr/bin/env bash
# One command instead of two: regenerate worker/public/data/results.json
# (export_results.py, including the live moomoo section -- needs OpenD
# running/logged in, see moomoo_client.py) and deploy it to the live
# dashboard (wrangler deploy). Run from the repo root:
#
#     ./update_dashboard.sh
#
# Manual/on-demand by design -- see README for why this isn't a cron job.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PYTHON="python3"
if [ -x ".venv/bin/python" ]; then
	PYTHON=".venv/bin/python"
fi

echo "==> Regenerating worker/public/data/results.json ($PYTHON export_results.py)"
"$PYTHON" export_results.py

echo "==> Deploying (wrangler deploy)"
(cd worker && wrangler deploy)

echo "==> Done."
