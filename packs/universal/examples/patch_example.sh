#!/usr/bin/env bash
#
# Example maintenance script — customize for your environment.
#
set -euo pipefail

LOG_TAG="st2-universal-example"
echo "[$LOG_TAG] Starting patch_example.sh"

if [[ -f /etc/os-release ]]; then
  # shellcheck source=/dev/null
  . /etc/os-release
  echo "[$LOG_TAG] Detected OS: ${NAME:-unknown} ${VERSION_ID:-}"
fi

echo "[$LOG_TAG] Completed successfully."
exit 0
