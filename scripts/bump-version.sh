#!/usr/bin/env bash
# ── bump-version.sh ── Bump versions for integration and/or bridge ────────────
#
# Usage:
#   ./scripts/bump-version.sh <component> <version|patch|minor|major>
#
# Components:
#   integration   Custom component (custom_components/polyconnect/manifest.json)
#   bridge        Add-on bridge (polyconnect_bridge/config.yaml + server.py)
#   all           Both (default if omitted)
#
# Examples:
#   ./scripts/bump-version.sh integration patch    # integration 2.0.0 → 2.0.1
#   ./scripts/bump-version.sh bridge minor         # bridge 2.0.3 → 2.1.0
#   ./scripts/bump-version.sh all 3.0.0            # both → 3.0.0
#   ./scripts/bump-version.sh patch                # shorthand: all patch
#
# Show current versions:
#   ./scripts/bump-version.sh status

set -euo pipefail

# ── Resolve project root (script lives in scripts/) ──────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── File paths ────────────────────────────────────────────────────────────────
MANIFEST="$ROOT/custom_components/polyconnect/manifest.json"
ADDON_CONFIG="$ROOT/polyconnect_bridge/config.yaml"
SERVER_PY="$ROOT/polyconnect_bridge/server.py"
README="$ROOT/docs/README.md"
API_REF="$ROOT/docs/api-reference.md"

# ── Helpers ───────────────────────────────────────────────────────────────────
die() { echo "ERROR: $*" >&2; exit 1; }

get_integration_version() {
    grep -oP '(?<="version": ")[^"]+' "$MANIFEST"
}

get_bridge_version() {
    grep -oP '(?<=^version: ")[^"]+' "$ADDON_CONFIG"
}

bump_semver() {
    local current="$1" part="$2"
    local major minor patch
    IFS='.' read -r major minor patch <<< "$current"

    case "$part" in
        major) echo "$((major + 1)).0.0" ;;
        minor) echo "${major}.$((minor + 1)).0" ;;
        patch) echo "${major}.${minor}.$((patch + 1))" ;;
        *) die "Unknown bump type: $part (expected major|minor|patch)" ;;
    esac
}

validate_semver() {
    [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Invalid semver: $1"
}

resolve_version() {
    local current="$1" arg="$2"
    case "$arg" in
        patch|minor|major) bump_semver "$current" "$arg" ;;
        *) validate_semver "$arg"; echo "$arg" ;;
    esac
}

show_status() {
    local int_ver bridge_ver
    int_ver="$(get_integration_version)"
    bridge_ver="$(get_bridge_version)"
    echo "Current versions:"
    echo "  integration : $int_ver  (manifest.json)"
    echo "  bridge      : $bridge_ver  (config.yaml + server.py)"
    if [[ "$int_ver" != "$bridge_ver" ]]; then
        echo ""
        echo "  ⚠ Versions are out of sync"
    fi
}

bump_integration() {
    local new_ver="$1"
    local current
    current="$(get_integration_version)"

    if [[ "$new_ver" == "$current" ]]; then
        echo "  integration already at $current — skipped"
        return
    fi

    sed -i "s/\"version\": \"[^\"]*\"/\"version\": \"$new_ver\"/" "$MANIFEST"
    # docs/README.md — integration line, discriminated by "IoT class"
    sed -i "s|\(\*\*Version:\*\* \)[0-9]\+\.[0-9]\+\.[0-9]\+\( · \*\*IoT class:\*\*\)|\1$new_ver\2|" "$README"
    echo "  ✓ integration: $current → $new_ver  ($MANIFEST, $README)"
}

bump_bridge() {
    local new_ver="$1"
    local current
    current="$(get_bridge_version)"

    if [[ "$new_ver" == "$current" ]]; then
        echo "  bridge already at $current — skipped"
        return
    fi

    sed -i "s/^version: \"[^\"]*\"/version: \"$new_ver\"/" "$ADDON_CONFIG"
    sed -i "s/^BRIDGE_VERSION = \"[^\"]*\"/BRIDGE_VERSION = \"$new_ver\"/" "$SERVER_PY"
    # docs/README.md — bridge line, discriminated by "Ports"
    sed -i "s|\(\*\*Version:\*\* \)[0-9]\+\.[0-9]\+\.[0-9]\+\( · \*\*Ports:\*\*\)|\1$new_ver\2|" "$README"
    # docs/api-reference.md — /health example response
    sed -i "s/\(\"version\": \"\)[0-9]\+\.[0-9]\+\.[0-9]\+\(\",\)/\1$new_ver\2/" "$API_REF"
    echo "  ✓ bridge: $current → $new_ver  ($ADDON_CONFIG, $SERVER_PY, $README, $API_REF)"
}

# Grep for any stale version references anywhere in the tree.
# Catches future doc/code that adds hardcoded versions the sed patterns miss.
verify_no_stragglers() {
    local int_ver bridge_ver
    int_ver="$(get_integration_version)"
    bridge_ver="$(get_bridge_version)"

    # Find every X.Y.Z-looking string in tracked source files, then filter
    # to only those NOT matching the current integration/bridge versions.
    local stragglers
    stragglers=$(grep -rEn -o '[0-9]+\.[0-9]+\.[0-9]+' \
        --include='*.py' --include='*.json' --include='*.yaml' --include='*.yml' --include='*.md' \
        --exclude-dir=.git --exclude-dir=.claude --exclude-dir=__pycache__ --exclude-dir=node_modules \
        "$ROOT" 2>/dev/null \
        | grep -viE '(hacs\.json|hassfest|homeassistant/|api-versio|python|playwright|chromium|firefox|mitmproxy|flask|node|npm)' \
        | grep -E "version|Version|VERSION" \
        | grep -vE ":${int_ver}$|:${bridge_ver}$" || true)

    if [[ -n "$stragglers" ]]; then
        echo ""
        echo "  ⚠ Possible stale version references found (verify manually):"
        echo "$stragglers" | sed 's/^/    /'
        echo ""
        echo "  If any of these are hardcoded versions that should track the bump,"
        echo "  add them to bump_integration() or bump_bridge() in this script."
    fi
}

usage() {
    echo "Usage: $0 [component] <version|patch|minor|major>"
    echo "       $0 status"
    echo ""
    echo "Components: integration | bridge | all (default)"
    echo ""
    echo "Examples:"
    echo "  $0 integration patch"
    echo "  $0 bridge 2.1.0"
    echo "  $0 all minor"
    echo "  $0 patch              # shorthand for: all patch"
    echo "  $0 status             # show current versions"
    exit 1
}

# ── Parse arguments ───────────────────────────────────────────────────────────
[[ $# -lt 1 ]] && usage

# Handle "status" command
if [[ "$1" == "status" ]]; then
    show_status
    exit 0
fi

# Determine component and version arg
COMPONENT="all"
VERSION_ARG=""

if [[ $# -eq 1 ]]; then
    # Single arg: treat as version/bump for "all"
    VERSION_ARG="$1"
elif [[ $# -eq 2 ]]; then
    case "$1" in
        integration|bridge|all) COMPONENT="$1"; VERSION_ARG="$2" ;;
        *) die "Unknown component: $1 (expected integration|bridge|all)" ;;
    esac
else
    usage
fi

# ── Resolve new versions ─────────────────────────────────────────────────────
echo ""

case "$COMPONENT" in
    integration)
        CURRENT="$(get_integration_version)"
        NEW="$(resolve_version "$CURRENT" "$VERSION_ARG")"
        bump_integration "$NEW"
        ;;
    bridge)
        CURRENT="$(get_bridge_version)"
        NEW="$(resolve_version "$CURRENT" "$VERSION_ARG")"
        bump_bridge "$NEW"
        ;;
    all)
        INT_CURRENT="$(get_integration_version)"
        BRIDGE_CURRENT="$(get_bridge_version)"
        INT_NEW="$(resolve_version "$INT_CURRENT" "$VERSION_ARG")"
        BRIDGE_NEW="$(resolve_version "$BRIDGE_CURRENT" "$VERSION_ARG")"
        bump_integration "$INT_NEW"
        bump_bridge "$BRIDGE_NEW"
        ;;
esac

echo ""
echo "Done."

verify_no_stragglers

echo ""
echo "Next steps:"
case "$COMPONENT" in
    integration)
        echo "  git add -A && git commit -m \"chore(integration): bump to $NEW\""
        echo "  git tag integration-v$NEW"
        ;;
    bridge)
        echo "  git add -A && git commit -m \"chore(bridge): bump to $NEW\""
        echo "  git tag bridge-v$NEW"
        ;;
    all)
        echo "  git add -A && git commit -m \"chore: bump integration=$INT_NEW bridge=$BRIDGE_NEW\""
        echo "  git tag integration-v$INT_NEW && git tag bridge-v$BRIDGE_NEW"
        ;;
esac
echo "  git push && git push --tags"
