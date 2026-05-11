#!/usr/bin/env bash
set -euo pipefail

ps -eo pid,ppid,user,%cpu,%mem,cmd --sort=-%cpu
exit 0
