#!/usr/bin/env bash
# DO NOT RUN BEFORE 20:30 (after the final submission). Deletes the downloaded data and checks the git history for secrets.
#   solution/tools/cleanup.sh              # lists what it will delete, asks for confirmation, then deletes
#   BASE=/path/to/hackdata solution/tools/cleanup.sh
# Targets under $BASE (default ~/hackdata): data, data_dev*, wt_data. Nothing inside the git repo is deleted.
set -uo pipefail
BASE=${BASE:-$HOME/hackdata}
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

shopt -s nullglob
TARGETS=("$BASE"/data "$BASE"/data_dev* "$BASE"/wt_data)
EXIST=(); for t in "${TARGETS[@]}"; do [ -e "$t" ] && EXIST+=("$t"); done
if [ ${#EXIST[@]} -eq 0 ]; then echo "Nothing to delete under $BASE."; else
  echo "About to DELETE (irreversible):"; du -sh "${EXIST[@]}" 2>/dev/null | sed 's/^/   /'
  printf 'Type DELETE to confirm: '; read -r ans
  if [ "$ans" = "DELETE" ]; then rm -rf -- "${EXIST[@]}" && echo "deleted."; else echo "aborted, nothing deleted."; fi
fi

echo; echo "== secrets check in git history of $REPO (all branches)"
cd "$REPO" || exit 1
bad=0
ENVS=$(git log --all --name-only --pretty=format: | grep -E '(^|/)\.env($|\.)' | sort -u)
if [ -n "$ENVS" ]; then echo "FOUND .env files in history:"; echo "$ENVS" | sed 's/^/   /'; bad=1; else echo "no .env files in history"; fi
KEYS=$(git log --all -p -I'.' --pretty=format: | grep -nIE \
  '(sk-ant-[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(api[_-]?key|secret|passwd|password|token)["'"'"' ]*[:=]["'"'"' ]*[A-Za-z0-9_\-]{16,})' | head -20)
if [ -n "$KEYS" ]; then echo "POSSIBLE secrets in history (first 20 lines, review them):"; echo "$KEYS" | cut -c1-160 | sed 's/^/   /'; bad=1; else echo "no key-like strings in history"; fi
TREE=$(git grep -nIE '(sk-ant-[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY)' -- . 2>/dev/null | head -5)
[ -n "$TREE" ] && { echo "POSSIBLE secrets in the working tree:"; echo "$TREE" | cut -c1-160; bad=1; }
[ $bad -eq 0 ] && echo "OK: no keys or .env found." || echo "REVIEW the findings above before publishing."
exit $bad
