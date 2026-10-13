#!/bin/sh
set -eu

# Existing Grafana volumes hide image files under /var/lib/grafana. Copy the
# built panel on startup while preserving other installed plugins and settings.
panel_dir="${GF_PATHS_PLUGINS:-/var/lib/grafana/plugins}/paymenthub-evidence-panel"
mkdir -p "$panel_dir"
cp -R /opt/paymenthub-evidence-panel/. "$panel_dir/"
exec /run.sh "$@"
