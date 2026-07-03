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

# Unit tests
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run pytest tests/unit -q'

# Integration tests (network-hitting; gated)
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && MAGICAPPLY_LIVE_TESTS=1 uv run pytest tests/integration'

# Playwright browsers — one-time; do not automate this
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run playwright install chromium'

# CLI
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run magicapply --help'
ssh magicapply-dev 'export PATH=$HOME/.local/bin:$PATH; cd ~/magicapply && uv run magicapply discover senior-swe'
```

## virtiofs share

Mounted automatically by fstab on first access to `~/magicapply` (systemd-automount). No manual `mount` needed after boot.

```
magicapply  /home/ggray/magicapply  virtiofs  defaults,nofail,x-systemd.automount  0  0
```

The mount depends on two settings that were tuned when the VM was first brought up — do not regress them without testing:

- **Guest kernel ≥ 6.12** (backports). The stock Debian 12 kernel (6.1) triggers a `WARN` at `virtio_fs_get_tree` and returns `EIO`; the mount never completes.
- **Domain XML `<driver type='virtiofs' queue='1024'/>`** — this becomes `queue-size=1024` on QEMU's `vhost-user-fs-pci` device. Anything smaller than ~128 causes the guest driver to reject the device before FUSE_INIT. The initial cloud-init used `queue='1'`, which is why the share never worked before the kernel upgrade + XML fix.

If both are in place, the virtiofsd log at `/tmp/virtiofsd-magicapply.log` (world-readable when the wrapper is set up for debug) should show `Client connected, servicing requests` and continued request lines when the guest performs I/O.

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
| `MAGICAPPLY_LIVE_APPLY` | Additional gate for the live-apply E2E test (see `tests/integration/e2e/test_e2e_live.py`) |
| `MAGICAPPLY_LINKEDIN_TESTS` | Gate for the `linkedin` marker (ToS-sensitive scraping) |
| `MAGICAPPLY_HOME` | Overrides the config-root lookup precedence in `src/magicapply/config/paths.py` |
| `LINKEDIN_LI_AT` | Session cookie for the deferred LinkedIn adapter |
| `UV_LINK_MODE=copy` | Suppress uv's hardlink warning; `.venv` on virtiofs, cache on local disk |

## Related

- `dryrun_plan.md` (repo root) — the phased plan for the end-to-end dry-run test harness.
- `docs/GOF_PATTERNS.md` — pattern mapping. Read before adding abstractions.
- `ARCHITECTURE.md` (repo root) — the design document these VM decisions serve.
