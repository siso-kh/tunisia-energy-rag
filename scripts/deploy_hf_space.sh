#!/usr/bin/env bash
# Push the current branch to the Hugging Face Docker Space.
#
# A Space reads a YAML metadata header from the very top of README.md. GitHub
# renders that same block as a table of config keys sitting above the project
# title, which is why README.md here carries no header. This script keeps the
# header in one place and injects it only into the commit that is pushed to the
# Space, leaving both the GitHub README and local history untouched.
#
# Usage:
#   bash scripts/deploy_hf_space.sh             # push HEAD to the `space` remote, main
#   bash scripts/deploy_hf_space.sh --dry-run   # build it, print it, push nothing
#
# Requires:
#   git remote add space https://huggingface.co/spaces/<user>/<space>
# See DEPLOY_HF_SPACE.md for the full runbook.
set -euo pipefail

cd "$(dirname "$0")/.."

REMOTE="${HF_SPACE_REMOTE:-space}"
BRANCH="${HF_SPACE_BRANCH:-main}"
DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

# The metadata block Hugging Face reads when configuring the Space.
read -r -d '' FRONTMATTER <<'YAML' || true
---
title: Tunisia Energy RAG
emoji: ⚡
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---
YAML

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  echo "error: git remote '$REMOTE' not found." >&2
  echo "       git remote add $REMOTE https://huggingface.co/spaces/<user>/<space>" >&2
  exit 1
fi

# Build the Space README without touching the working tree: hash the prefixed
# content into the object store, then graft it into a throwaway index. This
# keeps `git status` clean and avoids checking out large LFS assets into a
# temporary worktree.
BUILD_DIR=$(mktemp -d)
trap 'rm -rf "$BUILD_DIR"' EXIT

{ printf '%s\n\n' "$FRONTMATTER"; cat README.md; } > "$BUILD_DIR/README.md"

STAGED_INDEX="$BUILD_DIR/index"
export GIT_INDEX_FILE="$STAGED_INDEX"
git read-tree HEAD
BLOB=$(git hash-object -w "$BUILD_DIR/README.md")
git update-index --add --cacheinfo "100644,$BLOB,README.md"
TREE=$(git write-tree)
COMMIT=$(git commit-tree "$TREE" -p HEAD -m "Deploy to Hugging Face Space (inject Space metadata header)")
unset GIT_INDEX_FILE

if [ "$DRY_RUN" = "1" ]; then
  echo "--- Space README.md (first 18 lines) ---"
  head -18 "$BUILD_DIR/README.md"
  echo "--- commit $COMMIT would be pushed to $REMOTE/$BRANCH (dry run: nothing pushed) ---"
  exit 0
fi

git push "$REMOTE" "$COMMIT:$BRANCH"
echo "Pushed to $REMOTE/$BRANCH - watch the build in the Space Logs tab."