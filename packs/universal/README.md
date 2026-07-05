# universal

StackStorm pack for passwordless SSH automation with Git-hosted artifacts.

It supports:
- Running remote bash scripts from GitHub/Git with arguments.
- Running Ansible playbooks from GitHub/Git with inventory and extra vars.
- Fetching SSH key material from Delinea Secret Server or Azure Key Vault.

## Contents

| Component | Purpose |
|-----------|---------|
| `actions/run_remote_script.py` | Clone repo + run `.sh` on remote host over SSH |
| `actions/run_ansible_playbook.py` | Clone repo + run `ansible-playbook` with inventory |
| `actions/get_delinea_secret.py` | Fetch username/password/private key from Delinea |
| `actions/get_delinea_private_key.py` | Fetch private key (and username) from Delinea |
| `actions/get_azure_key_vault_private_key.py` | Fetch private key from Azure Key Vault |
| `actions/workflows/execute_script.yaml` | Orquesta workflow for Delinea + script execution |

## Requirements

- StackStorm with Python 3 action runners
- `git` on the StackStorm runner
- `ansible-playbook` on the runner (for `run_ansible_playbook`)
- Network access from runner to Git host, vault(s), and target hosts

Python dependencies in `requirements.txt`:
- `requests`
- `paramiko`
- `PyYAML`

## Configuration

Run:

```bash
st2 pack config universal
```

Example:

```yaml
---
# Delinea (required only for Delinea actions)
delinea_url: "https://your-tenant.secretservercloud.com"
delinea_client_id: "your-oauth-client-id"
delinea_client_secret: "your-oauth-client-secret"

# Azure Key Vault (required only for Key Vault action)
azure_auth_mode: "managed_identity"  # or "client_credentials"
azure_managed_identity_client_id: "" # set for user-assigned MI; empty = system-assigned
azure_tenant_id: "00000000-0000-0000-0000-000000000000"
azure_client_id: "00000000-0000-0000-0000-000000000000"
azure_client_secret: "your-azure-client-secret"
azure_key_vault_url: "https://my-vault.vault.azure.net"

# Git + SSH defaults
github_token: ""
allowed_scripts: []
ssh_port: 22
git_clone_timeout: 600
delinea_request_timeout: 60
azure_request_timeout: 60
ssh_known_hosts_path: "/home/stanley/.ssh/stackstorm_known_hosts"
ssh_strict_host_key_checking: false
```

## Actions

### `universal.run_remote_script`
Runs a repo-hosted `.sh` file on a remote target via SSH.

Key parameters:
- `github_repo`, `github_branch`, `script_path`
- target selection: `target_host` OR (`hostfile_path` + `host_entry`)
- credentials: `delinea_secret_id` OR (`ssh_username` + `ssh_password|ssh_private_key`)
- `script_args` (new): ordered list of script parameters

Example:

```bash
st2 run universal.run_remote_script \
  github_repo=https://github.com/org/scripts.git \
  github_branch=main \
  script_path=scripts/deploy.sh \
  target_host=10.0.0.15 \
  ssh_username=ec2-user \
  ssh_private_key="$(cat /path/to/id_rsa)" \
  script_args='["prod","v1.2.3"]'
```

### `universal.run_ansible_playbook`
Runs a repo-hosted Ansible playbook with a repo-hosted inventory.

Key parameters:
- `github_repo`, `github_branch`, `playbook_path`, `inventory_path`
- optional SSH overrides: `ssh_username`, `ssh_private_key`, `ssh_port`
- Ansible options: `extra_vars`, `limit`, `tags`, `skip_tags`, `check_mode`, `diff_mode`

Example:

```bash
st2 run universal.run_ansible_playbook \
  github_repo=https://github.com/org/infra.git \
  github_branch=main \
  playbook_path=playbooks/site.yml \
  inventory_path=inventory/prod.ini \
  ssh_username=ec2-user \
  ssh_private_key="$(cat /path/to/id_rsa)" \
  extra_vars='{"release":"2026.07.05","env":"prod"}' \
  limit=web
```

### `universal.get_delinea_private_key`
Fetches SSH private key (and username when present) from Delinea by `secret_id`.

### `universal.get_azure_key_vault_private_key`
Fetches SSH private key from Azure Key Vault by `secret_name` (optional `secret_version`).

Auth supports:
- `azure_auth_mode: managed_identity` for VM-assigned identity (IMDS token flow).
- `azure_auth_mode: client_credentials` for app registration (`azure_tenant_id`, `azure_client_id`, `azure_client_secret`).

### `universal.get_delinea_secret`
Fetches normalized `ssh_username`, `ssh_password`, and `ssh_private_key` from Delinea.

## Workflow

### `universal.execute_script`
Orquesta workflow:
1. `get_delinea_secret`
2. `run_remote_script`

Now also accepts and passes `script_args`.

## Security notes

- Secrets are not logged by action code.
- Script/inventory/playbook paths are repository-relative and traversal-protected.
- SSH host key behavior is controlled by `ssh_strict_host_key_checking` and `ssh_known_hosts_path`.
- Temporary clone folders and temporary key files are cleaned up after execution.
