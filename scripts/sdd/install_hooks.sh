#!/bin/sh
set -eu

sdd_repository_root=$(git rev-parse --show-toplevel)
cd "$sdd_repository_root"

for sdd_hook in .githooks/pre-commit .githooks/pre-push; do
  if [ ! -f "$sdd_hook" ]; then
    printf 'Missing required hook: %s\n' "$sdd_hook" >&2
    exit 1
  fi
  chmod +x "$sdd_hook"
done

chmod +x scripts/sdd/check_design_drift.py scripts/sdd/claude_stop_guard.py
git config --local core.hooksPath .githooks

sdd_configured_path=$(git config --local --get core.hooksPath)
if [ "$sdd_configured_path" != ".githooks" ]; then
  printf 'Failed to configure core.hooksPath; got: %s\n' "$sdd_configured_path" >&2
  exit 1
fi

printf 'Installed ZHIYI SDD Git hooks (core.hooksPath=.githooks).\n'
