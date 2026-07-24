#!/usr/bin/env bash
set -euo pipefail

PR_TITLE="$(jq -r '.pull_request.title // ""' "$GITHUB_EVENT_PATH")"
PR_NUMBER="$(jq -r '.pull_request.number // 0' "$GITHUB_EVENT_PATH")"

if [[ "$PR_TITLE" != "Capture shadow build run" ]]; then
    find soh -type f \( -name "*.c" -o -name "*.cpp" -o \( \( -name "*.h" -o -name "*.hpp" \) ! -path "soh/src/*" ! -path "soh/include/*" \) \) ! -path "soh/assets/*" -print0 | xargs -0 clang-format-14 -i --verbose
    exit 0
fi

RUN_JSON="$(gh run list --repo julainoloca00-dot/Shipwright-Shadow_Map --workflow generate-builds.yml --event workflow_dispatch --limit 10 --json databaseId,status,conclusion,createdAt,headBranch,headSha --jq '[.[] | select(.headBranch == "wind-waker-style-cel-shading")][0]')"
if [[ -z "$RUN_JSON" || "$RUN_JSON" == "null" ]]; then
    echo 'No workflow_dispatch build was found' >&2
    exit 1
fi

git fetch origin wind-waker-style-cel-shading
git checkout -B wind-waker-style-cel-shading origin/wind-waker-style-cel-shading
printf '%s\n' "$RUN_JSON" > .last-shadow-build-run.json

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git add .last-shadow-build-run.json
git commit -m 'Record ultra shadow Windows build run'
git push origin HEAD:wind-waker-style-cel-shading

if [[ "$PR_NUMBER" != "0" ]]; then
    gh pr close "$PR_NUMBER" --repo julainoloca00-dot/Shipwright-Shadow_Map --delete-branch --comment 'ID da execução Windows registrado temporariamente na celshade.' || true
fi
