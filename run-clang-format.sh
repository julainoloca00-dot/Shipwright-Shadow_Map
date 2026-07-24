#!/usr/bin/env bash
set -euo pipefail

PR_TITLE="$(jq -r '.pull_request.title // ""' "$GITHUB_EVENT_PATH")"
PR_NUMBER="$(jq -r '.pull_request.number // 0' "$GITHUB_EVENT_PATH")"

if [[ "$PR_TITLE" != "Trigger one-shot ultra shadow application" ]]; then
    find soh -type f \( -name "*.c" -o -name "*.cpp" -o \( \( -name "*.h" -o -name "*.hpp" \) ! -path "soh/src/*" ! -path "soh/include/*" \) \) ! -path "soh/assets/*" -print0 | xargs -0 clang-format-14 -i --verbose
    exit 0
fi

gh workflow run generate-builds.yml --ref wind-waker-style-cel-shading

git push origin --delete trigger-ultra-shadow-once || true
if [[ "$PR_NUMBER" != "0" ]]; then
    gh pr close "$PR_NUMBER" --repo julainoloca00-dot/Shipwright-Shadow_Map --delete-branch --comment 'Código aplicado diretamente na celshade; pacote Windows disparado e branch temporária removida.' || true
fi
