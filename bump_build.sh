#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION_FILE="$PROJECT_DIR/version.properties"

if [ ! -f "$VERSION_FILE" ]; then
    printf "VERSION_MAJOR=1\nVERSION_MINOR=0\nBUILD_NUMBER=2\n" > "$VERSION_FILE"
fi

VER_MAJOR=$(grep '^VERSION_MAJOR=' "$VERSION_FILE" | cut -d= -f2 | tr -d '[:space:]')
VER_MINOR=$(grep '^VERSION_MINOR=' "$VERSION_FILE" | cut -d= -f2 | tr -d '[:space:]')
CUR_BUILD=$(grep '^BUILD_NUMBER=' "$VERSION_FILE" | cut -d= -f2 | tr -d '[:space:]')
VER_MAJOR=${VER_MAJOR:-1}
VER_MINOR=${VER_MINOR:-0}
CUR_BUILD=${CUR_BUILD:-2}
NEXT_BUILD=$((CUR_BUILD + 1))

printf "VERSION_MAJOR=%s\nVERSION_MINOR=%s\nBUILD_NUMBER=%s\n" "$VER_MAJOR" "$VER_MINOR" "$NEXT_BUILD" > "$VERSION_FILE"
echo "📦 Bumped version to v${VER_MAJOR}.${VER_MINOR}.${NEXT_BUILD} (Build #${NEXT_BUILD})"
