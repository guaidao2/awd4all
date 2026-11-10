#!/usr/bin/env bash
#
# 版本快照脚本：把「上一个 tag 到当前工作树」的全部变动压成一个提交，再打 tag 推送。
# 不是软件发行流程，不发 GitHub Release，不产出安装包，只保证远端每个版本一个提交。
#
#   scripts/publish.sh 0.1.1
#   scripts/publish.sh 0.1.0 --force        # 重发刚发的版本：删远端旧 tag、强制覆盖历史
#   SKIP_PUSH=1 scripts/publish.sh 0.1.1    # 只本地压提交打 tag，不推
#
# 远端只会看到每个版本一个提交，没有中间开发提交。本地随便提交，发布时会被压掉。
set -euo pipefail

VERSION="${1:?用法: scripts/publish.sh <版本号，例如 0.1.1>}"
VERSION="${VERSION#v}"
TAG="v$VERSION"
BRANCH="${RELEASE_BRANCH:-main}"
FORCE=0
for arg in "$@"; do
    [ "$arg" = "--force" ] && FORCE=1
done

cd "$(git rev-parse --show-toplevel)"

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    if [ "$FORCE" = "1" ]; then
        echo "重发 $TAG：删掉本地和远端的旧 tag，历史会被覆盖"
        git tag -d "$TAG" >/dev/null
        GIT_TERMINAL_PROMPT=0 git push origin ":refs/tags/$TAG" >/dev/null 2>&1 || true
    else
        echo "tag $TAG 已经存在；要重发就加 --force，否则换个版本号。" >&2
        exit 1
    fi
fi

python - "$VERSION" <<'PY'
import re
import sys
from pathlib import Path

version = sys.argv[1]
pyproject = Path("pyproject.toml")
pyproject.write_text(
    re.sub(r'^version = ".*"', f'version = "{version}"',
           pyproject.read_text(encoding="utf-8"), flags=re.MULTILINE),
    encoding="utf-8",
)
Path("awd4all/version.py").write_text(f'version = "{version}"\n', encoding="utf-8")
print(f"版本号已写为 {version}")
PY

git add -A
TREE="$(git write-tree)"

PREV_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
if [ -n "$PREV_TAG" ]; then
    PARENT="$(git rev-list -n 1 "$PREV_TAG")"
    COMMIT="$(git commit-tree "$TREE" -p "$PARENT" -m "release: $TAG")"
    echo "已把 $PREV_TAG 之后的变动压成一个提交（父提交 $PREV_TAG）"
else
    COMMIT="$(git commit-tree "$TREE" -m "release: $TAG")"
    echo "首次提交，创建单提交历史"
fi

git update-ref "refs/heads/$BRANCH" "$COMMIT"
git tag -a "$TAG" -m "awd4all $TAG"
echo "提交: $(git log -1 --format='%h %s' "$COMMIT")"
echo "tag : $TAG"

if [ "${SKIP_PUSH:-0}" != "1" ]; then
    if [ "$FORCE" = "1" ]; then
        GIT_TERMINAL_PROMPT=0 git push --force origin "$BRANCH" --tags
    else
        GIT_TERMINAL_PROMPT=0 git push origin "$BRANCH" --tags
    fi
    echo "已推送到 origin/$BRANCH 和 $TAG"
else
    echo "SKIP_PUSH=1，没有推送。手动推送：git push origin $BRANCH --tags"
fi
