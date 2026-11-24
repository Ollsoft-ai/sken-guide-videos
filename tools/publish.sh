#!/usr/bin/env bash
# Commit rendered clips (mp4/webm + per-clip json) from $1 into clips/ and push to main.
set -euo pipefail
src="$1"
shopt -s nullglob
mkdir -p clips
n=0
for f in "$src"/*.mp4 "$src"/*.webm "$src"/*.json; do
  [ "$(basename "$f")" = results.json ] && continue
  cp "$f" clips/
  n=$((n + 1))
done
[ "$n" -gt 0 ] || { echo "nothing to publish"; exit 0; }
git config user.name "github-actions[bot]"
git config user.email "41898283+github-actions[bot]@users.noreply.github.com"
git add clips
if git diff --cached --quiet; then echo "clips unchanged"; exit 0; fi
git commit -q -m "clips: ${GITHUB_WORKFLOW:-local} run ${GITHUB_RUN_ID:-?}"
for i in 1 2 3 4 5; do
  if git pull -q --rebase origin main && git push -q origin HEAD:main; then echo "pushed"; exit 0; fi
  sleep $((i * 7))
done
exit 1
