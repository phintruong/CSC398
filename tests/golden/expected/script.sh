#!/usr/bin/env bash
# Golden: nginx port fix
# Generated from recorded terminal sessions (python -m generate script). Review before running.
# Every step cites the events it came from. Run with DRY_RUN=1 to print the steps without running them.
set -euo pipefail

run() { echo "+ $*" >&2; if [ "${DRY_RUN:-0}" != 1 ]; then "$@"; fi; }
# eval, not a subshell: `source`, `export` and `cd` in a step must affect the steps after it
sh_run() { echo "+ $1" >&2; if [ "${DRY_RUN:-0}" != 1 ]; then eval "$1"; fi; }

# Step 1: Ran `sudo sed -i 's/listen 8080 default_server$/listen 8080 default_server;/' default`.  [imp-61f28cd04a26#c3]
run cd "/etc/nginx/sites-available"
sh_run "sudo sed -i 's/listen 8080 default_server\$/listen 8080 default_server;/' default"

# Step 2: Ran `sudo nginx -t`.  [imp-61f28cd04a26#c4]
sh_run "sudo nginx -t"

# Step 3: Ran `sudo systemctl reload nginx`.  [imp-61f28cd04a26#c5]
sh_run "sudo systemctl reload nginx"

# Step 4: Ran `curl -sI http://localhost:8080 | head -1`.  [imp-61f28cd04a26#c6]
sh_run "curl -sI http://localhost:8080 | head -1"

echo "done" >&2
