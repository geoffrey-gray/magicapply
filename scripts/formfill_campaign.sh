#!/usr/bin/env bash
# Form-fill dry-run campaign: exercise ATS handlers up to (not including) submit.
#
# Goal: ~TARGET_APPLIES dry-run applies over ~DURATION_HOURS, record every
# outcome (APPLIED dry_run / FAILED / NEEDS_INTERVENTION) for form-fill tuning.
#
# Always --no-submit. Never --yes-submit.
#
# Usage:
#   ./scripts/formfill_campaign.sh
#   TARGET_APPLIES=50 DURATION_HOURS=15 PROFILE=multi-dryrun ./scripts/formfill_campaign.sh
#
# Progress: data/formfill_campaign.log + data/formfill_campaign.status
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${PATH}"

CONFIG_ROOT="${CONFIG_ROOT:-configs}"
PROFILE="${PROFILE:-multi-dryrun}"
TARGET_APPLIES="${TARGET_APPLIES:-50}"
DURATION_HOURS="${DURATION_HOURS:-15}"
# ~50 over 15h ≈ one apply every 18 minutes (pace under global hourly=4)
INTERVAL_SEC="${INTERVAL_SEC:-1080}"
MAX_APPLIES_PER_TICK="${MAX_APPLIES_PER_TICK:-1}"

LOG="${ROOT}/data/formfill_campaign.log"
STATUS="${ROOT}/data/formfill_campaign.status"
START_MARKER="${ROOT}/data/formfill_campaign.started"
mkdir -p "${ROOT}/data"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

count_campaign_outcomes() {
  CONFIG_ROOT="${CONFIG_ROOT}" START_ISO="${START_ISO}" uv run python - <<'PY'
import os
from datetime import datetime, UTC
from pathlib import Path
from magicapply.config import load_config
from magicapply.cli.composition import build_repos
from magicapply.domain.models.application import ApplicationState

loaded = load_config(Path(os.environ["CONFIG_ROOT"]).resolve())
_, apps = build_repos(loaded.data_dir())
start = datetime.fromisoformat(os.environ["START_ISO"].replace("Z", "+00:00"))
if start.tzinfo is None:
    start = start.replace(tzinfo=UTC)

applied_dry = failed = intervention = 0
for state, attr in (
    (ApplicationState.APPLIED, "applied_dry"),
    (ApplicationState.FAILED, "failed"),
    (ApplicationState.NEEDS_INTERVENTION, "intervention"),
):
    for a in apps.list_by_state(state):
        # Count rows touched after campaign start (updated_at)
        ts = a.updated_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < start:
            continue
        if state is ApplicationState.APPLIED:
            if a.dry_run:
                applied_dry += 1
        elif state is ApplicationState.FAILED:
            failed += 1
        else:
            intervention += 1

# Also count any apply attempt that left an error on tailored? no — only terminals.
print(f"{applied_dry} {failed} {intervention}")
PY
}

write_status() {
  local applied_dry="$1" failed="$2" intervention="$3" note="$4"
  local total=$((applied_dry + failed + intervention))
  cat >"$STATUS" <<EOF
updated_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
profile=${PROFILE}
target_applies=${TARGET_APPLIES}
duration_hours=${DURATION_HOURS}
started_utc=${START_ISO}
applied_dry_run=${applied_dry}
failed=${failed}
needs_intervention=${intervention}
outcomes_total=${total}
note=${note}
EOF
}

if [[ ! -f "$START_MARKER" ]]; then
  date -u +%Y-%m-%dT%H:%M:%SZ >"$START_MARKER"
fi
START_ISO="$(cat "$START_MARKER")"
START_EPOCH="$(python3 -c "from datetime import datetime; print(int(datetime.fromisoformat('${START_ISO}'.replace('Z','+00:00')).timestamp()))")"
END_EPOCH=$((START_EPOCH + DURATION_HOURS * 3600))

log "campaign start profile=${PROFILE} target=${TARGET_APPLIES} duration_h=${DURATION_HOURS} interval_s=${INTERVAL_SEC} started=${START_ISO}"

while true; do
  NOW_EPOCH="$(date -u +%s)"
  if (( NOW_EPOCH >= END_EPOCH )); then
    log "time budget exhausted (${DURATION_HOURS}h)"
    break
  fi

  read -r APPLIED_DRY FAILED INTERVENTION <<<"$(count_campaign_outcomes)"
  TOTAL=$((APPLIED_DRY + FAILED + INTERVENTION))
  write_status "$APPLIED_DRY" "$FAILED" "$INTERVENTION" "pre-tick"
  log "progress applied_dry=${APPLIED_DRY} failed=${FAILED} intervention=${INTERVENTION} total_outcomes=${TOTAL}/${TARGET_APPLIES}"

  if (( TOTAL >= TARGET_APPLIES )); then
    log "target reached (${TOTAL} >= ${TARGET_APPLIES})"
    break
  fi

  # Prefer apply-only when we already have a tailored backlog (faster form-fill loop).
  TAILORED_N="$(
    CONFIG_ROOT="${CONFIG_ROOT}" PROFILE="${PROFILE}" uv run python - <<'PY'
import os
from pathlib import Path
from magicapply.config import load_config
from magicapply.cli.composition import build_repos
from magicapply.domain.models.application import ApplicationState
loaded = load_config(Path(os.environ["CONFIG_ROOT"]).resolve())
_, apps = build_repos(loaded.data_dir())
print(len(apps.list_by_state_and_profile(ApplicationState.TAILORED, os.environ["PROFILE"])))
PY
  )"

  if [[ "${TAILORED_N}" -lt 3 ]]; then
    log "low tailored backlog (${TAILORED_N}); discover+tailor"
    if uv run magicapply discover "${PROFILE}" --root "${CONFIG_ROOT}" >>"$LOG" 2>&1; then
      log "discover ok"
    else
      log "WARN discover failed"
    fi
    if uv run magicapply tailor "${PROFILE}" --root "${CONFIG_ROOT}" >>"$LOG" 2>&1; then
      log "tailor ok"
    else
      log "WARN tailor failed"
    fi
  else
    log "tailored backlog=${TAILORED_N}; skip discover this tick"
    # Still tailor any SCORED leftovers cheaply
    uv run magicapply tailor "${PROFILE}" --root "${CONFIG_ROOT}" >>"$LOG" 2>&1 || true
  fi

  JOB_ID="$(
    ROBOT_SELECT_PROFILE="${PROFILE}" CONFIG_ROOT="${CONFIG_ROOT}" uv run python - <<'PY'
import os
from pathlib import Path
from magicapply.config import load_config
from magicapply.cli.composition import build_repos
from magicapply.domain.models.application import ApplicationState
from magicapply.infrastructure.sources.apply_url import (
    is_external_apply_url,
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

board_hosts = ("linkedin.com", "indeed.com", "glassdoor.com")

def rank(app):
    job = jobs_repo.get(app.job_id)
    if job is None:
        return (3, app.job_id)
    dest = resolve_job_apply_destination(job)
    au = (job.apply_url or "").strip()
    if is_external_apply_url(au, board_hosts=board_hosts) or (
        dest and not is_job_board_listing_url(dest)
    ):
        return (0, dest or app.job_id)
    if dest or au or job.url:
        return (1, app.job_id)
    return (2, app.job_id)

print(sorted(tailored, key=rank)[0].job_id)
PY
  )" || true

  if [[ -z "${JOB_ID:-}" ]]; then
    log "no TAILORED jobs; will discover next interval"
  else
    log "apply --no-submit job_id=${JOB_ID}"
    if uv run magicapply apply "${JOB_ID}" --root "${CONFIG_ROOT}" --no-submit >>"$LOG" 2>&1; then
      log "apply finished job_id=${JOB_ID}"
    else
      log "WARN apply non-zero exit job_id=${JOB_ID} (outcome should still be recorded)"
    fi
  fi

  read -r APPLIED_DRY FAILED INTERVENTION <<<"$(count_campaign_outcomes)"
  TOTAL=$((APPLIED_DRY + FAILED + INTERVENTION))
  write_status "$APPLIED_DRY" "$FAILED" "$INTERVENTION" "post-tick"
  log "post-tick applied_dry=${APPLIED_DRY} failed=${FAILED} intervention=${INTERVENTION} total=${TOTAL}/${TARGET_APPLIES}"

  if (( TOTAL >= TARGET_APPLIES )); then
    log "target reached after tick"
    break
  fi

  NOW_EPOCH="$(date -u +%s)"
  if (( NOW_EPOCH >= END_EPOCH )); then
    log "time budget exhausted after tick"
    break
  fi

  # Sleep remaining interval, but not past end.
  SLEEP_FOR=$INTERVAL_SEC
  REMAIN=$((END_EPOCH - NOW_EPOCH))
  if (( SLEEP_FOR > REMAIN )); then
    SLEEP_FOR=$REMAIN
  fi
  log "sleep ${SLEEP_FOR}s until next tick"
  sleep "${SLEEP_FOR}"
done

read -r APPLIED_DRY FAILED INTERVENTION <<<"$(count_campaign_outcomes)"
write_status "$APPLIED_DRY" "$FAILED" "$INTERVENTION" "finished"
log "campaign finished applied_dry=${APPLIED_DRY} failed=${FAILED} intervention=${INTERVENTION}"
