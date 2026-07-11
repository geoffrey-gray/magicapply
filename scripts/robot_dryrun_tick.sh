#!/usr/bin/env bash
# One tick of the always-on dry-run robot.
#
# Philosophy:
#   - 2–4 applies/hour (global throttle hourly=4)
#   - Rotate discovery profile by 6h UTC window
#   - Record every try; never --yes-submit
#   - Incomplete market coverage is fine
#
# Cron (every 15 minutes → up to ~4 applies/hour):
#   */15 * * * * cd /path/to/magicapply && ./scripts/robot_dryrun_tick.sh >> data/robot.log 2>&1
#
# Overrides:
#   ROBOT_PROFILE=multi-dryrun CONFIG_ROOT=configs ./scripts/robot_dryrun_tick.sh
#   MAX_APPLIES_PER_TICK=1 SKIP_DISCOVER=1  # apply-only tick
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${PATH}"

CONFIG_ROOT="${CONFIG_ROOT:-configs}"
MAX_APPLIES_PER_TICK="${MAX_APPLIES_PER_TICK:-1}"
SKIP_DISCOVER="${SKIP_DISCOVER:-0}"
SKIP_TAILOR="${SKIP_TAILOR:-0}"

# 6-hour rotation windows (UTC). Glassdoor deferred (login/security) —
# mid-day slot uses multi-dryrun (LinkedIn + Indeed) instead.
HOUR_UTC="$(date -u +%H)"
HOUR_UTC=$((10#$HOUR_UTC))
if   (( HOUR_UTC < 6 ));  then PROFILE="dryrun-li-gh"
elif (( HOUR_UTC < 12 )); then PROFILE="dryrun-indeed"
elif (( HOUR_UTC < 18 )); then PROFILE="multi-dryrun"
else                          PROFILE="dryrun-ats-only"
fi
PROFILE="${ROBOT_PROFILE:-$PROFILE}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

log "tick profile=${PROFILE} config_root=${CONFIG_ROOT}"

if [[ "${SKIP_DISCOVER}" != "1" ]]; then
  if uv run magicapply discover "${PROFILE}" --root "${CONFIG_ROOT}"; then
    log "discover ok"
  else
    log "WARN discover failed (continue — other sources / later window)"
  fi
fi

if [[ "${SKIP_TAILOR}" != "1" ]]; then
  if uv run magicapply tailor "${PROFILE}" --root "${CONFIG_ROOT}"; then
    log "tailor ok"
  else
    log "WARN tailor failed (continue)"
  fi
fi

# Select one TAILORED job for this profile (prefer external ATS apply_url)
JOB_ID="$(
  ROBOT_SELECT_PROFILE="${PROFILE}" CONFIG_ROOT="${CONFIG_ROOT}" uv run python - <<'PY'
import os
from pathlib import Path

from magicapply.config import load_config
from magicapply.cli.composition import build_repos
from magicapply.domain.models.application import ApplicationState
from magicapply.infrastructure.sources.apply_url import (
    is_job_board_listing_url,
    resolve_job_apply_destination,
)

root = Path(os.environ.get("CONFIG_ROOT", "configs")).resolve()
profile = os.environ["ROBOT_SELECT_PROFILE"]
loaded = load_config(root)
jobs_repo, apps_repo = build_repos(loaded.data_dir())
tailored = apps_repo.list_by_state_and_profile(ApplicationState.TAILORED, profile)
if not tailored:
    raise SystemExit(0)

# Prefer real employer/ATS destinations; never burn a tick on board listings.
candidates = []
for app in tailored:
    job = jobs_repo.get(app.job_id)
    if job is None:
        continue
    dest = resolve_job_apply_destination(job)
    if is_job_board_listing_url(dest):
        continue
    candidates.append(app.job_id)

if not candidates:
    raise SystemExit(0)
print(sorted(candidates)[0])
PY
)" || true

if [[ -z "${JOB_ID:-}" ]]; then
  log "no TAILORED jobs for profile=${PROFILE}; tick done"
  exit 0
fi

n=0
while (( n < MAX_APPLIES_PER_TICK )); do
  log "apply --no-submit job_id=${JOB_ID} profile=${PROFILE}"
  if uv run magicapply apply "${JOB_ID}" --root "${CONFIG_ROOT}" --no-submit; then
    log "apply finished job_id=${JOB_ID}"
  else
    log "WARN apply non-zero exit job_id=${JOB_ID} (outcome should still be recorded)"
  fi
  n=$((n + 1))
  # only one selection per tick unless we re-query; keep simple
  break
done

log "tick complete profile=${PROFILE}"
