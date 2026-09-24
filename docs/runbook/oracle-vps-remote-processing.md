# Runbook: Remote Processing V1 on an Oracle Ubuntu VPS

Written for: the operator of the VPS (you), running these by hand over SSH.

Every command below is meant to be copy-pasted. Nothing here was executed for
you: this session had no SSH access to the VPS and invented no credentials.

The design decisions this runbook assumes are recorded in
`docs/adr/0009-remote-processing-v1.md`:

- the API binds **loopback only** and is reached through an SSH tunnel, so no new
  public port is opened and no certificate is needed;
- API and worker run as **native systemd units**, not containers.

## 0. Before you start

You need:

- SSH access to the VPS as a sudo-capable user;
- the Supabase service credential for `vorquel-content-brain`;
- roughly 3× the size of your largest recording free on the data disk
  (the uploaded object, the extracted audio, and working room).

Check the disk first. Ingest refuses an upload that would leave less than 2 GB
free, and a full disk is the most likely way this deployment breaks:

```bash
df -h /var
```

## 1. System dependencies

`av` and `rapidocr` ship wheels, but FFmpeg's shared libraries and a compiler for
any source fallback still need to be present:

```bash
sudo apt-get update
sudo apt-get install -y \
  python3.12 python3.12-venv python3.12-dev \
  git build-essential pkg-config \
  ffmpeg libsndfile1
```

Confirm the interpreter is the pinned one. The lock is resolved for 3.12:

```bash
python3.12 --version   # expect Python 3.12.x
```

## 2. Service user and directories

The service user owns the data and nothing else. It gets no login shell, so a
compromise of the API does not hand over an interactive session:

```bash
sudo useradd --system --create-home --home-dir /home/vorquel \
  --shell /usr/sbin/nologin vorquel

sudo mkdir -p /opt/vorquel-watch /var/lib/vorquel-watch /etc/vorquel-watch
sudo chown -R vorquel:vorquel /opt/vorquel-watch /var/lib/vorquel-watch

# Config is root-owned: the service reads it, the service cannot rewrite it.
sudo chown root:vorquel /etc/vorquel-watch
sudo chmod 750 /etc/vorquel-watch
```

## 3. Install the application

```bash
sudo -u vorquel git clone \
  https://github.com/guilhermehenriquedasilva915-design/Vorquel-watch.git \
  /opt/vorquel-watch/src

cd /opt/vorquel-watch/src
sudo -u vorquel git checkout v0.1.0-alpha.1   # or the reviewed remote tag

sudo -u vorquel python3.12 -m venv /opt/vorquel-watch/.venv

# --require-hashes is not optional. It is the supply-chain control the whole
# dependency policy rests on.
sudo -u vorquel /opt/vorquel-watch/.venv/bin/python -m pip install \
  --require-hashes -r /opt/vorquel-watch/src/backend/requirements.lock

sudo -u vorquel /opt/vorquel-watch/.venv/bin/python -m pip install \
  -e /opt/vorquel-watch/src/backend --no-deps
```

Verify the runtime imports before wiring services:

```bash
sudo -u vorquel /opt/vorquel-watch/.venv/bin/python -c \
  "import av, faster_whisper, rapidocr, vorquel_watch.remote_api; print('runtime ok')"
```

## 4. Secrets

DPAPI does not exist on Linux, so `credentials.is_supported()` is False there and
the Supabase secret comes from the environment. That is the same path CI uses.
It must therefore live in a root-owned 0600 file, never in the unit file.

Generate the API token on the VPS and read it once:

```bash
sudo install -m 600 -o root -g vorquel /dev/null /etc/vorquel-watch/watch.env

# 48 URL-safe characters. Well above the 32-character minimum the API enforces.
TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"

sudo tee /etc/vorquel-watch/watch.env >/dev/null <<EOF
VORQUEL_WATCH_SUPABASE_URL=https://xnygwzuckijaxzlszfpn.supabase.co
VORQUEL_WATCH_SUPABASE_SECRET_KEY=PASTE_THE_SUPABASE_SERVICE_KEY_HERE
VORQUEL_WATCH_REMOTE_TOKEN=${TOKEN}
EOF

sudo chmod 600 /etc/vorquel-watch/watch.env
sudo chown root:vorquel /etc/vorquel-watch/watch.env

# Copy this into the notebook's credential store now; then stop echoing it.
echo "API token: ${TOKEN}"
```

Edit the file to paste the real Supabase key:

```bash
sudo nano /etc/vorquel-watch/watch.env
```

Confirm the permissions are what you think they are:

```bash
sudo ls -l /etc/vorquel-watch/watch.env   # expect -rw------- root vorquel
```

## 5. Install and start the services

```bash
sudo cp /opt/vorquel-watch/src/deploy/systemd/vorquel-watch-api.service \
        /opt/vorquel-watch/src/deploy/systemd/vorquel-watch-worker.service \
        /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now vorquel-watch-worker
sudo systemctl enable --now vorquel-watch-api
```

`enable` is what survives a reboot. Verify that explicitly rather than trusting
it:

```bash
systemctl is-enabled vorquel-watch-api vorquel-watch-worker
```

## 6. Status, logs, stop, start

```bash
# status
sudo systemctl status vorquel-watch-api --no-pager
sudo systemctl status vorquel-watch-worker --no-pager

# logs: structured JSON events, already redacted of secrets and paths
sudo journalctl -u vorquel-watch-api -n 100 --no-pager
sudo journalctl -u vorquel-watch-worker -f

# stop / start / restart
sudo systemctl stop vorquel-watch-api
sudo systemctl start vorquel-watch-api
sudo systemctl restart vorquel-watch-worker
```

Liveness check on the VPS itself:

```bash
curl -fsS http://127.0.0.1:8787/v1/health   # {"ok":true}
```

That endpoint is the only unauthenticated route and it returns a constant, so it
reveals nothing about configuration or contents.

## 7. Firewall

Nothing to open. This is the point of the loopback design.

```bash
# Confirm the API is NOT listening on a public interface.
sudo ss -tlnp | grep 8787   # expect 127.0.0.1:8787, never 0.0.0.0:8787
```

Leave the Oracle security list and `ufw` untouched. Port 22 you already have.

> If you later decide to expose the API publicly, that is a firewall and DNS
> change with a real internet-facing attack surface. Do not improvise it from
> this runbook — it needs its own decision and its own review.

## 8. Notebook setup

On the notebook, open the tunnel. Everything else assumes it is up:

```powershell
ssh -N -L 8787:127.0.0.1:8787 ubuntu@YOUR_VPS_IP
```

Store the token once, in the OS credential store rather than a file:

```powershell
vorquel-watch remote configure
# paste the token printed in step 4
```

Then use it:

```powershell
vorquel-watch remote upload "C:\workshops\workshop.mp4"
# -> source_id, job_id, job_status QUEUED

# close the laptop here; the VPS keeps going

vorquel-watch remote list
vorquel-watch remote status job_xxxxxxxx
vorquel-watch remote cancel job_xxxxxxxx
```

Re-uploading the same recording transfers no bytes: the client hashes the file,
asks whether the host already holds that digest, and reports the existing
`source_id` instead.

## 9. Safe update

```bash
cd /opt/vorquel-watch/src
sudo -u vorquel git fetch --tags

# Stop the API first so no upload lands mid-update. The worker is stopped second
# so it can finish heartbeating; an interrupted job is reclaimed on restart
# anyway, and transcription resumes at its last committed chunk.
sudo systemctl stop vorquel-watch-api
sudo systemctl stop vorquel-watch-worker

sudo -u vorquel git checkout THE_NEW_TAG
sudo -u vorquel /opt/vorquel-watch/.venv/bin/python -m pip install \
  --require-hashes -r /opt/vorquel-watch/src/backend/requirements.lock
sudo -u vorquel /opt/vorquel-watch/.venv/bin/python -m pip install \
  -e /opt/vorquel-watch/src/backend --no-deps

sudo systemctl start vorquel-watch-worker
sudo systemctl start vorquel-watch-api

sudo journalctl -u vorquel-watch-worker -n 50 --no-pager
```

Apply any new Supabase migrations before starting the new version, in the order
`supabase/README.md` gives. Remote Processing V1 itself adds none.

## 10. Troubleshooting

| Symptom | Cause | Check |
|---|---|---|
| API exits immediately | no token, or one under 32 chars | `journalctl -u vorquel-watch-api -n 20` |
| Every request returns 401 | notebook token differs from the VPS token | re-run `remote configure` |
| Upload returns 507 | less than 2 GB free after the upload | `df -h /var` |
| Upload returns 415 | container or codec not on the allowlist | `docs/adr/0002`, `source_guard.py` |
| Upload returns 411 | client sent no `Content-Length` | use the CLI, not raw `curl` |
| Jobs stay QUEUED | worker is down | `systemctl status vorquel-watch-worker` |
| Connection refused from notebook | tunnel dropped | re-open the `ssh -L` tunnel |

To see what the host is holding without a notebook:

```bash
sudo -u vorquel env $(sudo cat /etc/vorquel-watch/watch.env | xargs) \
  /opt/vorquel-watch/.venv/bin/vorquel-watch remote list --url http://127.0.0.1:8787
```
