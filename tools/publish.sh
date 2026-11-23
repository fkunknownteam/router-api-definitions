#!/usr/bin/env bash
# Create the GitHub repository and push. Needs a token with `repo` scope.
#
#   GH_TOKEN=ghp_xxx bash tools/publish.sh <owner> [repo-name]
#
# Nothing here is stored; the token is used for these two calls and then dropped.
set -euo pipefail

OWNER="${1:?usage: GH_TOKEN=... bash tools/publish.sh <owner> [repo]}"
REPO="${2:-router-api-definitions}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${GH_TOKEN:?set GH_TOKEN to a token with repo scope}"
command -v jq >/dev/null || { echo "jq is required"; exit 1; }

echo "creating $OWNER/$REPO"
code=$(curl -sS -o /tmp/gh_create.json -w '%{http_code}' \
  -X POST https://api.github.com/user/repos \
  -H "Authorization: Bearer $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -d "$(jq -n --arg n "$REPO" '{
        name: $n,
        description: "Machine-readable router API definitions: one JSON document per vendor/model/firmware, executed by a generic client",
        private: false,
        has_issues: true,
        has_wiki: false,
        has_projects: false
      }')")

case "$code" in
  201) echo "created" ;;
  422) echo "already exists, will push into it" ;;
  *)   echo "create failed (HTTP $code):"; cat /tmp/gh_create.json; exit 1 ;;
esac

cd "$ROOT"
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$OWNER/$REPO.git"

# push without embedding the token in .git/config
git -c credential.helper= \
    -c "http.https://github.com/.extraheader=Authorization: Bearer $GH_TOKEN" \
    push -u origin main

echo
echo "pushed. next:"
echo "  raw base  https://raw.githubusercontent.com/$OWNER/$REPO/main/registry"
echo "  actions   https://github.com/$OWNER/$REPO/actions"
echo "  repo      https://github.com/$OWNER/$REPO"
echo
echo "the app's registry constant must match:"
echo "  $OWNER/$REPO"
