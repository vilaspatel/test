# remote_bash

StackStorm pack that **shallow-clones** a GitHub repository, validates a `.sh` script path, uploads it to a **Linux VM over SSH**, runs `chmod +x` and `/bin/bash`, and returns stdout/stderr/exit code.

Unlike the `universal` pack, there is **no Secret Server**: SSH credentials are supplied as action parameters — **`ssh_username`** plus **`ssh_password`** *or* **`ssh_private_key`** (both marked `secret` in the action schema).

## Features

- Git clone via subprocess (**no `shell=True`**); optional **`github_token`** in pack config for private HTTPS repos
- Password or PEM key authentication (**Paramiko**)
- Optional **hostfile** in the repo (YAML/JSON) to resolve `target_host` / port by logical name
- Path traversal checks; optional **`allowed_scripts`** allowlist in pack config
- Strict SSH host keys (`RejectPolicy` after loading system + optional `ssh_known_hosts_path`)

## Install

```bash
st2 pack install file:///path/to/packs/remote_bash
# or copy under /opt/stackstorm/packs/remote_bash then:
sudo /opt/stackstorm/st2/bin/st2packs.setup_virtualenv --pack remote_bash
sudo /opt/stackstorm/st2/bin/st2ctl reload --register-all
```

## Configuration (`st2 pack config remote_bash`)

| Key | Purpose |
|-----|---------|
| `github_token` | PAT for private GitHub clones over HTTPS (secret) |
| `allowed_scripts` | Optional list of allowed script basenames or paths |
| `ssh_port` | Default SSH port (default `22`) |
| `git_clone_timeout` | Clone timeout seconds (default `600`) |
| `ssh_known_hosts_path` | Extra `known_hosts` file path |

## Action: `remote_bash.run_remote_script`

**Required:** `github_repo`, `github_branch`, `script_path`, **`ssh_username`**, and **either** `ssh_password` **or** `ssh_private_key`.

**Target:** `target_host` **or** (`hostfile_path` + `host_entry`).

**Port precedence:** action `ssh_port` → hostfile entry → pack `ssh_port` → `22`.

### Example (password)

```bash
st2 run remote_bash.run_remote_script \
  github_repo=https://github.com/org/scripts.git \
  github_branch=main \
  script_path=examples/hello.sh \
  target_host=10.0.0.5 \
  ssh_username=ubuntu \
  ssh_password='YourVmPasswordHere' \
  timeout=3600
```

### Example (hostfile in repo)

```bash
st2 run remote_bash.run_remote_script \
  github_repo=https://github.com/org/scripts.git \
  github_branch=main \
  script_path=examples/hello.sh \
  hostfile_path=inventory/hosts.yaml \
  host_entry=linux-prod-01 \
  ssh_username=ubuntu \
  ssh_password='YourVmPasswordHere'
```

### Return payload

Includes `success`, `stdout`, `stderr`, `exit_code`, `resolved_host`, `ssh_port`, `remote_script_path`, and when applicable `hostfile_path` / `host_entry`.

## Security notes

- Passwords and keys are **StackStorm secret parameters** — still avoid passing them in shell history where possible; prefer rules/API with encrypted datastore references where your deployment supports it.
- Pre-load target SSH host keys (`ssh-keyscan`) for the StackStorm runner user.
- Do **not** put passwords in Git hostfiles — only hostnames/ports there.

## Examples in this pack

- `examples/hello.sh`, `examples/patch_example.sh`
- `examples/hosts.example.yaml` — inventory shape for `hostfile_path`
