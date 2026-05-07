# universal

Production-oriented StackStorm pack for cloning bash scripts from GitHub and executing them on remote Linux hosts over SSH. SSH credentials are retrieved from **Delinea Secret Server** at runtime (OAuth2 client credentials). Git cloning supports shallow clones, branch selection, optional GitHub PAT for private repos, strict script-path validation, SFTP upload to `/tmp/st2-script-<timestamp>.sh`, `chmod +x`, and `/bin/bash` execution with stdout/stderr/exit code capture.

## Contents

| Component | Purpose |
|-----------|---------|
| `actions/get_delinea_secret.py` | OAuth token + secret retrieval |
| `actions/run_remote_script.py` | Git clone, validation, SSH upload/execute |
| `actions/workflows/execute_script.yaml` | Orquesta workflow (secret → remote run) |
| `actions/lib/delinea_client.py` | Reusable Delinea REST client |
| `actions/lib/github_client.py` | Git subprocess shallow clone (no `shell=True`) |
| `actions/lib/hostfile.py` | YAML/JSON host inventory parsing |
| `actions/lib/ssh_client.py` | Paramiko SSH/SFTP with timeouts |
| `examples/*.sh` | Sample scripts you can host in a repo |
| `examples/hosts.example.yaml` | Example Git-hosted host inventory |

## Requirements

- StackStorm with Python 3 action runners  
- `git` available on the StackStorm runner host  
- Network access from the runner to GitHub, Delinea, and target SSH endpoints  
- Writable home for the StackStorm runtime user so **`~/.ssh/known_hosts`** can be updated on first connect (see **SSH host keys**), unless you enable strict-only mode

Python dependencies are declared in `requirements.txt` (`requests`, `paramiko`, `PyYAML`).

## Installation

1. Copy or clone this directory onto your StackStorm server (or CI artifact storage).

   ```bash
   sudo cp -r packs/universal /opt/stackstorm/packs/universal
   sudo chown -R root:st2packs /opt/stackstorm/packs/universal
   ```

2. Install pack dependencies into the pack virtualenv:

   ```bash
   sudo /opt/stackstorm/st2/bin/st2packs.setup_virtualenv --pack universal
   ```

3. Register and enable:

   ```bash
   sudo /opt/stackstorm/st2/bin/st2 pack setup universal
   sudo /opt/stackstorm/st2/bin/st2ctl reload --register-all
   ```

   Alternatively, from a tarball/git checkout:

   ```bash
   st2 pack install file:///path/to/packs/universal
   ```

## Configuration

Configure the pack (values stored encrypted where marked `secret: true`):

```bash
st2 pack config universal
```

Example `config.yaml` (paths vary by install):

```yaml
---
# Delinea — omit entirely when using only inline SSH creds (workflow passthrough / action params)
delinea_url: "https://your-tenant.secretservercloud.com"
delinea_client_id: "your-oauth-client-id"
delinea_client_secret: "your-oauth-client-secret"
github_token: ""                       # optional PAT for private repos
allowed_scripts: []                    # optional basename allowlist, e.g. ["deploy.sh"]
ssh_port: 22
git_clone_timeout: 600
delinea_request_timeout: 60
ssh_known_hosts_path: "/home/stanley/.ssh/stackstorm_known_hosts"  # optional; else ~/.ssh/known_hosts
ssh_strict_host_key_checking: false  # default: auto-accept & save new keys (see SSH host keys)
```

Pack config validation allows **no Delinea keys** in `universal.yaml`. Runtime checks still apply: `get_delinea_secret` and `run_remote_script` with `delinea_secret_id` require those settings to be present and complete.

### Delinea Secret Server setup

1. Create an OAuth **API** or machine-to-machine integration that supports **client credentials** against your Secret Server tenant.
2. Grant the integration permission to **read** the relevant secrets (SSH username + password *or* private key field).
3. Map secret fields so that the returned payload includes recognizable fields (username/password/key). This pack accepts typical **Username** / **Password** / **Private Key** style fields on `items[]`, or top-level `UserName` shortcuts returned by the REST layer.

The client calls:

- `POST /oauth2/token` with `grant_type=client_credentials`
- `GET /api/v1/secrets/{secretId}` with `Authorization: Bearer …`

If your tenant uses different routes, extend `DelineaClient` accordingly while keeping secrets out of logs.

### GitHub PAT setup

For **private** repositories, create a fine-grained or classic PAT with **contents: read**, store it as `github_token` in pack config (encrypted). HTTPS URLs are rewritten client-side to embed `x-access-token` authentication (the token value is never logged).

Public repos work without a token.

### Host inventory in Git (hostfile)

You can store hostnames (and optional per-host SSH ports) in the **same repository** as your scripts, instead of passing `target_host` each run.

1. Add a YAML, YML, or JSON file (repository-relative path), for example `inventory/hosts.yaml`. See `examples/hosts.example.yaml` for shape.
2. Pass **`hostfile_path`** (path inside the repo) and **`host_entry`** (the key to resolve).
3. Omit **`target_host`** when using a hostfile (if both are supplied, the hostfile entry wins and a log line notes that).

**Port precedence:** explicit action parameter **`ssh_port`** overrides the hostfile; otherwise **`port`** / **`ssh_port`** from the entry; otherwise pack **`ssh_port`** / default `22`.

Credentials remain in **Delinea** only—the hostfile must **not** contain passwords or private keys.

### SSH host keys

**Default (no extra config):** on first SSH to a new host, the action **accepts the server host key** (`AutoAddPolicy`), connects, then **`save_host_keys`** persists it to **`~/.ssh/known_hosts`** for the StackStorm runtime user (creating **`~/.ssh`** with mode `0700` if needed). If **`ssh_known_hosts_path`** is set, new keys are saved to **that same file** (and it is loaded together with system keys). No `ssh_auto_add_*` setting is required.

**Strict mode (optional):** set **`ssh_strict_host_key_checking: true`** in pack config. Then unknown hosts are **rejected** until their keys exist in `known_hosts` (use **`ssh-keyscan`** ahead of time for production).

If you still see **`not found in known_hosts`**, you likely enabled strict mode or the runtime user cannot write **`~/.ssh/known_hosts`** (permissions / read-only home).

`SSHClient` loads:

1. The StackStorm service user’s system `known_hosts`.
2. Optionally **`ssh_known_hosts_path`** from pack config.

## Security model

- **No plaintext credential logging** — only high-level events (e.g. “using caller-supplied credentials”, hostnames, non-sensitive IDs).
- **Path validation** — repository-relative paths only; rejects `..`, absolute paths; scripts must use `.sh`; hostfiles must use `.yaml`, `.yml`, or `.json`.
- **Allowlist** — optional `allowed_scripts` (basenames or full relative paths) merged with in-code `ALLOWED_SCRIPTS`.
- **Temporary artifacts** — shallow clone directories are removed after execution; remote script remains on target under `/tmp/` (rotation/cleanup is target-side responsibility).
- **No `shell=True`** — `git` uses argv lists; remote commands use `exec_command` with explicit quoting via `shlex.quote`.
- **SSH host keys** — by default the action accepts a new host key once and saves it under **`~/.ssh/known_hosts`** for the runtime user; set **`ssh_strict_host_key_checking: true`** only when you require keys to be pre-seeded (no automatic save).

### Recommendations

- Prefer SSH keys stored in Delinea over long-lived passwords where policy allows.
- Scope Delinea OAuth clients narrowly and rotate client secrets on schedule.
- Combine allowlists with repo CODEOWNERS/required reviews for scripts that run in production.
- Restrict outbound connectivity from the StackStorm runner and targets via firewall rules.

## Actions

### `universal.get_delinea_secret`

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `secret_id` | string | Delinea secret identifier |

**Returns** — `success`, `ssh_username`, `ssh_password`, `ssh_private_key` (sensitive), or `error`.

### `universal.run_remote_script`

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `github_repo` | string | Clone URL (`https://…`, `git@github.com:…`) |
| `github_branch` | string | Branch/tag for shallow clone |
| `script_path` | string | Repo-relative path to `.sh` file |
| `target_host` | string | Hostname/IP when **not** using a Git hostfile |
| `hostfile_path` | string | Repo-relative path to `.yaml` / `.yml` / `.json` inventory |
| `host_entry` | string | Key in that hostfile to resolve (`resolved_host`, optional port) |
| `timeout` | integer | SSH exec timeout seconds (default `3600`) |
| `delinea_secret_id` | string | Fetch credentials inside the action (standalone use) |
| `ssh_username` | string | Inline username (e.g. from workflow step 1) |
| `ssh_password` | string | Inline password (`secret: true`) |
| `ssh_private_key` | string | PEM private key (`secret: true`) |
| `ssh_port` | integer | Overrides hostfile/port defaults when set |

**Target:** either **`target_host`** **or** both **`hostfile_path`** and **`host_entry`**.

Either supply **`delinea_secret_id`** *or* **`ssh_username` + (`ssh_password` or `ssh_private_key`)**.

**Returns**

```json
{
  "success": true,
  "stdout": "...",
  "stderr": "...",
  "exit_code": 0,
  "remote_script_path": "/tmp/st2-script-1715000000.sh",
  "resolved_host": "10.0.0.5",
  "ssh_port": 22,
  "host_entry": "linux-prod-01",
  "hostfile_path": "inventory/hosts.yaml"
}
```

When `target_host` was used directly, `host_entry` and `hostfile_path` are `null`.

On failure, `success` is `false` and `error` explains the fault without leaking secrets.

## Workflow

### `universal.execute_script`

Orquesta workflow that runs `get_delinea_secret` then `run_remote_script`, passing normalized credential fields without embedding the secret ID in the second action’s logs beyond StackStorm’s normal execution audit trail.

**Workflow inputs**

- `github_repo`, `github_branch`, `script_path`, `delinea_secret_id`, `timeout` (defaults to `3600` via YAQL `coalesce` if omitted — requires YAQL `coalesce`; if your StackStorm build errors on this expression, replace it with a literal or set `timeout` explicitly).
- Optional: `target_host`, or **`hostfile_path`** + **`host_entry`** instead (same rules as the action).

## Example executions

Single action with inline Delinea fetch:

```bash
st2 run universal.run_remote_script \
  github_repo=https://github.com/org/scripts.git \
  github_branch=main \
  script_path=examples/hello.sh \
  target_host=10.0.0.5 \
  delinea_secret_id=linux-prod-01 \
  timeout=3600
```

Workflow (chains secret retrieval + execution):

```bash
st2 run universal.execute_script \
  github_repo=https://github.com/org/scripts.git \
  github_branch=main \
  script_path=examples/hello.sh \
  target_host=10.0.0.5 \
  delinea_secret_id=linux-prod-01 \
  timeout=3600
```

Using a **Git-hosted hostfile** (omit `target_host`; commit `inventory/hosts.yaml` in that repo):

```bash
st2 run universal.run_remote_script \
  github_repo=https://github.com/org/scripts.git \
  github_branch=main \
  script_path=examples/hello.sh \
  hostfile_path=inventory/hosts.yaml \
  host_entry=linux-prod-01 \
  delinea_secret_id=linux-prod-01 \
  timeout=3600
```

With optional allowlist configured:

```yaml
allowed_scripts: ["hello.sh", "patch_example.sh"]
```

```bash
st2 run universal.run_remote_script \
  github_repo=https://github.com/org/scripts.git \
  github_branch=main \
  script_path=examples/hello.sh \
  target_host=10.0.0.5 \
  delinea_secret_id=linux-prod-01
```

## Example scripts

See `examples/hello.sh` and `examples/patch_example.sh`. Place them under a path such as `examples/hello.sh` in your Git repository and reference that relative path in `script_path`.

For host inventories, copy `examples/hosts.example.yaml` into your repo (for example `inventory/hosts.yaml`) and reference it with `hostfile_path` / `host_entry`.

## Troubleshooting

| Symptom | Likely cause | Mitigation |
|---------|----------------|------------|
| `not found in known_hosts` / Paramiko rejection | **`ssh_strict_host_key_checking: true`** or cannot write `~/.ssh/known_hosts` | Set **`ssh_strict_host_key_checking: false`** (default) for auto-save, or pre-seed keys with **`ssh-keyscan`**, or fix permissions on `~/.ssh`. |
| `git clone` failure | Auth, DNS, or branch name | Verify PAT for private repos; confirm branch exists; check runner outbound HTTPS. |
| Delinea HTTP 401/403 | Wrong OAuth scopes or secret ID | Validate client credentials grant and RBAC on the secret. |
| `script is not in the allowlist` | `allowed_scripts` configured | Add basename or relative path, or clear allowlist for path-only checks. |
| `host entry not found in hostfile` | Wrong `host_entry` or branch/file mismatch | Confirm key exists in the file on `github_branch`; path matches `hostfile_path`. |
| Workflow YAQL errors on `coalesce` | Older Orquesta/YAQL | Pin explicit `timeout` in workflow input or replace expression with `<% ctx().timeout %>` and always pass `timeout`. |

### Workflow task result paths

If chaining tasks manually, StackStorm exposes nested action results as `task(<name>).result.result` for Python actions returning `(True, dict)`. If your environment differs, inspect `st2 execution get <id> --json` and adjust YAQL paths accordingly.

## Development notes

- Extend `DelineaClient._normalize_ssh_credentials` if your vault uses custom field names.
- For large repos, consider caching strategies outside this pack (artifact storage, sparse checkout — not implemented here).
- This pack targets Linux remotes; paths and quoting assume POSIX shells.

## License

Use and modify under your organization’s policies; no warranty expressed or implied.
