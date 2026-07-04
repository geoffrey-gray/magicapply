# Dev VM — `magicapply-dev`

MagicApply is developed on the host but built and run inside a dedicated libvirt VM (`magicapply-dev`). The source tree lives on the host and is shared into the VM via **virtiofs**, so host-side edits are immediately visible inside the VM — no push step.

## VM

| Setting | Value |
|---|---|
| libvirt domain | `magicapply-dev` (Debian 12 cloud image, 2 vCPU / 4 GB RAM) |
| Domain XML | `/mnt/storage/VMs/magicapply-dev-setup/magicapply-dev.xml` |
| Cloud-init | `/mnt/storage/VMs/magicapply-dev-setup/user-data` |
| IP | `192.168.122.6` (reserved via `virsh net-update`) |
| User | `ggray` |
| Guest kernel | 6.12+ (bookworm-backports `linux-image-cloud-amd64`) — required; the stock 6.1 kernel is incompatible with the host's virtiofsd 1.13 |
| Repo path (in VM) | `~/magicapply` — virtiofs share of `/mnt/storage/VMs/dev/magicapply` (automount via `fstab`) |
| Python | 3.12 via [uv](https://github.com/astral-sh/uv) at `~/.local/share/uv/` |
| Virtualenv | `~/magicapply/.venv` |

## SSH

Already configured in `~/.ssh/config` on the host:

```
Host magicapply-dev
  HostName 192.168.122.6
  User ggray
  IdentityFile ~/.ssh/id_ed25519
  StrictHostKeyChecking accept-new
```

Any command in this document that starts with `ssh magicapply-dev '...'` runs inside the VM.

## Environment gotchas for non-interactive SSH

Non-interactive SSH sessions (`ssh magicapply-dev '<cmd>'`) do **not** source `~/.bashrc`, so `uv` (installed under `~/.local/bin/`) is not on `PATH`. Also, `~/magicapply` lives on virtiofs while the uv cache lives on the VM's local disk, so `uv` can't hardlink between them and warns.

Set both in every non-interactive command:

```bash
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; cd ~/magicapply && uv sync'
```

Interactive shells (`ssh magicapply-dev` with no command) work as expected — `.bashrc` runs and everything is on `PATH`.

## Daily loop

```bash
# Sync deps (idempotent; safe to run every time)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH UV_LINK_MODE=copy; cd ~/magicapply && uv sync --extra dev'

# Unit tests (fast; ~2s)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests/unit -q'

# Full suite including the hermetic E2E fixture (needs Chromium)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests -q'

# Live-network integration tests (Anthropic + custom URL sources)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && MAGICAPPLY_LIVE_TESTS=1 uv run pytest tests/integration'

# CLI
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run magicapply --help'
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run magicapply doctor --root configs'
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run magicapply run senior-swe'                 # dry-run against real sources
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run magicapply run senior-swe --yes-submit'    # real submissions
```

## Playwright browsers (one-time setup)

The apply flow drives real Chromium via Playwright. Install once inside the VM — this download is ~120 MB and the runtime shared libraries are ~50 MB more:

```bash
# The browser binary (headless shell + full chromium)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run playwright install chromium'

# System runtime deps (libnspr4, libnss3, libatk-bridge2.0-0, libgbm1, …)
# Must be run with sudo because it apt installs system packages. Do NOT
# route this through `uv` because sudo does not inherit uv from PATH; call
# the venv Python directly.
ssh magicapply-dev 'sudo -n DEBIAN_FRONTEND=noninteractive /home/ggray/magicapply/.venv/bin/python -m playwright install-deps chromium'
```

After both steps `uv run magicapply doctor` should print `chromium PRESENT (…)` and the E2E fixture test should pass.

## virtiofs share

Mounted automatically by fstab on first access to `~/magicapply` (systemd-automount). No manual `mount` needed after boot.

```
magicapply  /home/ggray/magicapply  virtiofs  defaults,nofail,x-systemd.automount  0  0
```

The mount depends on two settings that were tuned when the VM was first brought up — do not regress them without testing:

- **Guest kernel ≥ 6.12** (backports). The stock Debian 12 kernel (6.1) triggers a `WARN` at `virtio_fs_get_tree` and returns `EIO`; the mount never completes.
- **Domain XML `<driver type='virtiofs' queue='1024'/>`** — this becomes `queue-size=1024` on QEMU's `vhost-user-fs-pci` device. Anything smaller than ~128 causes the guest driver to reject the device before FUSE_INIT. The initial cloud-init used `queue='1'`, which is why the share never worked before the kernel upgrade + XML fix.

The wrapper at `/mnt/storage/VMs/magicapply-dev-setup/virtiofsd-wrapper.sh` is intentionally minimal — just `--sandbox=none "$@"` — because NixOS doesn't grant virtiofsd the caps for the default namespace sandbox. If you need to debug a fresh mount failure, temporarily add `--log-level debug` and redirect stderr to a world-readable log; `magicapply-dev.qcow2` will pick it up on the next `virsh start`.

## Fallback: sync host checkout → VM

Only useful if virtiofs regressed or the disk was re-provisioned before the fixes above landed:

```bash
/mnt/storage/VMs/magicapply-dev-setup/sync-to-vm.sh
```

This tars the host checkout (excluding `.venv`, caches, and `data/`) over SSH into `~/magicapply` inside the VM. On a working virtiofs setup, using this is a mistake — it overwrites the mount with a stale snapshot.

## VM lifecycle

```bash
virsh -c qemu:///system start magicapply-dev
virsh -c qemu:///system shutdown magicapply-dev   # graceful
virsh -c qemu:///system destroy magicapply-dev    # force; use only if shutdown hangs
```

If the domain XML is edited (e.g. resizing memory, adjusting the virtiofs queue), redefine before restart:

```bash
virsh -c qemu:///system define /mnt/storage/VMs/magicapply-dev-setup/magicapply-dev.xml
```

## Env vars that matter

| Var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Required when `llm.provider: anthropic` in `configs/base_config.yaml`; unnecessary with `mock` or `replay` |
| `MAGICAPPLY_LIVE_TESTS` | Gate for the `integration` pytest marker |
| `MAGICAPPLY_LIVE_APPLY` | Extra opt-in for the live-apply E2E test (`tests/integration/e2e/test_e2e_live.py`) |
| `MAGICAPPLY_LIVE_APPLY_PROFILE` | Profile name the live-apply E2E should run against |
| `MAGICAPPLY_LIVE_APPLY_ROOT` | Optional `--root` override for the live-apply E2E |
| `MAGICAPPLY_LINKEDIN_TESTS` | Gate for the `linkedin` marker (ToS-sensitive scraping) |
| `MAGICAPPLY_LINKEDIN_ACK` | Set to `1` to acknowledge LinkedIn ToS risk; the LinkedIn adapter refuses to run without it |
| `MAGICAPPLY_INDEED_ACK` | Set to `1` to acknowledge Indeed ToS risk; the Indeed adapter refuses to run without it |
| `MAGICAPPLY_GLASSDOOR_ACK` | Set to `1` to acknowledge Glassdoor ToS risk; the Glassdoor adapter refuses to run without it |
| `GLASSDOOR_SESSION` | Optional Glassdoor session cookie value for authenticated views |
| `MAGICAPPLY_LLM_RECORD` | Set to `1` when running with `provider: replay` to capture new LLM responses into `configs/llm-fixtures/<key>.yaml` |
| `MAGICAPPLY_UPDATE_GOLDENS` | Set to `1` to rewrite `tests/goldens/tailoring/*` from the current run |
| `MAGICAPPLY_HOME` | Overrides the config-root lookup precedence in `src/magicapply/config/paths.py` |
| `LINKEDIN_LI_AT` | Session cookie for the deferred LinkedIn adapter |
| `UV_LINK_MODE=copy` | Suppress uv's hardlink warning; `.venv` on virtiofs, cache on local disk |

## Related

- `dryrun_plan.md` (repo root) — the phased plan for the end-to-end dry-run test harness.
- `docs/GOF_PATTERNS.md` — pattern mapping. Read before adding abstractions.
- `ARCHITECTURE.md` (repo root) — the design document these VM decisions serve.
