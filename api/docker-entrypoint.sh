#!/bin/sh
set -eu
dir="${RIVERBANK_DOCUMENT_DIR:-/var/lib/riverbank/statements}"
mkdir -p "$dir"
chown app:app "$dir"
exec runuser -u app -- "$@"
