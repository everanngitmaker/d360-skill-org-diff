#!/bin/bash

# -----------------------------------------------
# Data Cloud Diff Script
# Compares metadata between orgs and/or branches to detect drift and backlogs.
#
# Modes:
#   org-vs-branch  — compare live org against its env branch (detect manual org changes)
#   org-vs-org     — compare two live orgs (find changes backlogged between envs)
#   branch-vs-branch — compare two git branches (find changes backlogged in git)
#
# Usage:
#   ./scripts/4-compare.sh org-vs-branch <org>
#   ./scripts/4-compare.sh org-vs-org <org-a> <org-b>
#   ./scripts/4-compare.sh branch-vs-branch <branch-a> <branch-b>
#
# Known gap: org-side destructive changes (e.g. manually deleting a field map in the UI)
# are NOT detected, because Data Cloud's published data kit state still references them.
# -----------------------------------------------

MODE=$1

if [ -z "$MODE" ]; then
  echo "ERROR: Missing mode."
  echo ""
  echo "Usage:"
  echo "  ./scripts/4-compare.sh org-vs-branch <org>"
  echo "  ./scripts/4-compare.sh org-vs-org <org-a> <org-b>"
  echo "  ./scripts/4-compare.sh branch-vs-branch <branch-a> <branch-b>"
  exit 1
fi

# SCRIPT_DIR = where this script and diff_report.py live (may be the skill cache).
# PROJECT_ROOT = the user's project repo, resolved from the current git repo so the
# script can run in-place from anywhere as long as you're inside the project.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$PROJECT_ROOT" ]; then
  echo "ERROR: Not inside a git repository. cd into your project repo before running."
  exit 1
fi
MANIFESTS_DIR="$PROJECT_ROOT/manifests"
CONFIG_FILE="$PROJECT_ROOT/config/pipeline.config"

# Load pipeline config (needed to resolve org→branch mapping)
if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: pipeline.config not found at $CONFIG_FILE"
  exit 1
fi
source "$CONFIG_FILE"

resolve_env_branch() {
  # Given an org alias, return the mapped env branch from ORG_BRANCH_MAP
  local ORG_NAME=$1
  IFS=',' read -ra PAIRS <<< "$ORG_BRANCH_MAP"
  for PAIR in "${PAIRS[@]}"; do
    local ORG="${PAIR%%:*}"
    local BRANCH="${PAIR##*:}"
    if [ "$ORG" == "$ORG_NAME" ]; then
      echo "$BRANCH"
      return 0
    fi
  done
  return 1
}

retrieve_org_to_temp() {
  # Retrieve every manifest in manifests/ from the given org into a temp dir.
  # Echoes the temp dir path on success; returns non-zero on failure.
  local ORG=$1
  local TMPDIR
  TMPDIR=$(mktemp -d -t org-compare-retrieve-XXXX)

  cp "$PROJECT_ROOT/sfdx-project.json" "$TMPDIR/"
  mkdir -p "$TMPDIR/force-app/main/default"

  local MANIFEST_FILES=("$MANIFESTS_DIR"/*.xml)
  if [ ! -e "${MANIFEST_FILES[0]}" ]; then
    echo "ERROR: No manifest files found in $MANIFESTS_DIR" >&2
    rm -rf "$TMPDIR"
    return 1
  fi

  for MANIFEST in "${MANIFEST_FILES[@]}"; do
    local MANIFEST_NAME
    MANIFEST_NAME=$(basename "$MANIFEST")
    local WORK_MANIFEST="$TMPDIR/$MANIFEST_NAME"
    cp "$MANIFEST" "$WORK_MANIFEST"

    # Strip unsupported types (same rules as retrieve/deploy scripts)
    python3 -c "
import re
file = '$WORK_MANIFEST'
content = open(file).read()
for pattern in ['[Ee]xtDataTranFieldTemplate', 'DataPackageKitDefinition', 'DataPackageKitObject', 'DataKitObjectTemplate', 'DataKitObjectDependency', 'DataStreamTemplate']:
    content = re.sub(
        r'\n?\s*<types>(?:(?!</types>)[\s\S])*?<name>' + pattern + r'</name>(?:(?!</types>)[\s\S])*?</types>',
        '', content)
open(file, 'w').write(content)
"
    if [ $? -ne 0 ]; then
      echo "ERROR: Failed to strip unsupported types from $MANIFEST_NAME" >&2
      rm -rf "$TMPDIR"
      return 1
    fi

    echo "  Retrieving $MANIFEST_NAME from $ORG..." >&2
    (cd "$TMPDIR" && sf project retrieve start \
      --manifest "$MANIFEST_NAME" \
      --target-org "$ORG" \
      --wait 30 < /dev/null) >&2
    if [ $? -ne 0 ]; then
      echo "ERROR: Retrieve failed for $MANIFEST_NAME from $ORG" >&2
      rm -rf "$TMPDIR"
      return 1
    fi
  done

  # KQ_ fields are stripped during deploy (platform bug W-19660646); remove them
  # from the retrieved snapshot so diffs don't flag them as drift.
  find "$TMPDIR/force-app" -path "*/fields/KQ_*.field-meta.xml" -delete 2>/dev/null

  echo "$TMPDIR"
}

snapshot_branch_to_temp() {
  # Check out a branch (or ref) into a detached worktree and return its path.
  local REF=$1
  local TMPDIR
  TMPDIR=$(mktemp -d -t org-compare-branch-XXXX)
  rm -rf "$TMPDIR"  # git worktree add needs the dir to not exist

  git -C "$PROJECT_ROOT" worktree add --detach "$TMPDIR" "$REF" >&2
  if [ $? -ne 0 ]; then
    echo "ERROR: Failed to create worktree for $REF" >&2
    return 1
  fi

  # Match KQ_ handling in the org retrieve so the comparison is apples-to-apples
  find "$TMPDIR/force-app" -path "*/fields/KQ_*.field-meta.xml" -delete 2>/dev/null

  echo "$TMPDIR"
}

cleanup_worktree() {
  local DIR=$1
  [ -z "$DIR" ] && return
  git -C "$PROJECT_ROOT" worktree remove --force "$DIR" >/dev/null 2>&1
  rm -rf "$DIR"
}

print_drift_report() {
  # Generates an HTML drift report scoped to the manifests in manifests/,
  # prints a summary to the terminal, and opens the HTML in the default browser.
  local LEFT=$1
  local RIGHT=$2
  local LEFT_LABEL=$3
  local RIGHT_LABEL=$4

  # Output HTML lands in reports/ with a timestamp so runs don't overwrite each other.
  local REPORTS_DIR="$PROJECT_ROOT/reports"
  mkdir -p "$REPORTS_DIR"
  local STAMP
  STAMP=$(date +%Y%m%d-%H%M%S)
  local SAFE_LEFT=$(echo "$LEFT_LABEL"  | tr '/' '-' | tr -c 'A-Za-z0-9.-' '_')
  local SAFE_RIGHT=$(echo "$RIGHT_LABEL" | tr '/' '-' | tr -c 'A-Za-z0-9.-' '_')
  local REPORT_HTML="$REPORTS_DIR/drift-${SAFE_LEFT}_vs_${SAFE_RIGHT}-${STAMP}.html"

  python3 "$SCRIPT_DIR/diff_report.py" \
    "$LEFT" "$RIGHT" "$LEFT_LABEL" "$RIGHT_LABEL" \
    "$MANIFESTS_DIR" "$REPORT_HTML"
  local STATUS=$?

  if [ $STATUS -ne 0 ]; then
    echo "ERROR: Failed to generate drift report."
    return $STATUS
  fi

  # Open in the default browser (macOS), ignore failures on non-macOS
  if command -v open >/dev/null 2>&1; then
    open "$REPORT_HTML" >/dev/null 2>&1
  fi
}

# -----------------------------------------------
# Mode dispatch
# -----------------------------------------------

case "$MODE" in
  org-vs-branch)
    ORG=$2
    if [ -z "$ORG" ]; then
      echo "ERROR: org-vs-branch requires <org>"
      echo "Usage: ./scripts/4-compare.sh org-vs-branch <org>"
      exit 1
    fi
    ENV_BRANCH=$(resolve_env_branch "$ORG")
    if [ -z "$ENV_BRANCH" ]; then
      echo "ERROR: No branch mapping for org '$ORG' in pipeline.config"
      echo "→ Add an entry to ORG_BRANCH_MAP, e.g.: $ORG:<branch>"
      exit 1
    fi

    echo "Mode:         org-vs-branch"
    echo "Org:          $ORG"
    echo "Branch:       origin/$ENV_BRANCH"

    MANIFEST_FILES=("$MANIFESTS_DIR"/*.xml)
    if [ ! -e "${MANIFEST_FILES[0]}" ]; then
      echo "ERROR: No manifest files found in $MANIFESTS_DIR"
      exit 1
    fi

    echo ""
    echo "Fetching latest from remote..."
    git -C "$PROJECT_ROOT" fetch origin >/dev/null 2>&1

    echo ""
    echo "Retrieving org snapshot..."
    ORG_DIR=$(retrieve_org_to_temp "$ORG")
    [ -z "$ORG_DIR" ] && exit 1

    echo ""
    echo "Snapshotting branch..."
    BRANCH_DIR=$(snapshot_branch_to_temp "origin/$ENV_BRANCH")
    [ -z "$BRANCH_DIR" ] && { rm -rf "$ORG_DIR"; exit 1; }

    print_drift_report "$BRANCH_DIR" "$ORG_DIR" "origin/$ENV_BRANCH" "$ORG"

    cleanup_worktree "$BRANCH_DIR"
    rm -rf "$ORG_DIR"
    ;;

  org-vs-org)
    ORG_A=$2
    ORG_B=$3
    if [ -z "$ORG_A" ] || [ -z "$ORG_B" ]; then
      echo "ERROR: org-vs-org requires <org-a> <org-b>"
      echo "Usage: ./scripts/4-compare.sh org-vs-org <org-a> <org-b>"
      exit 1
    fi

    echo "Mode:         org-vs-org"
    echo "Org A:        $ORG_A"
    echo "Org B:        $ORG_B"

    MANIFEST_FILES=("$MANIFESTS_DIR"/*.xml)
    if [ ! -e "${MANIFEST_FILES[0]}" ]; then
      echo "ERROR: No manifest files found in $MANIFESTS_DIR"
      exit 1
    fi

    echo ""
    echo "Retrieving $ORG_A..."
    DIR_A=$(retrieve_org_to_temp "$ORG_A")
    [ -z "$DIR_A" ] && exit 1

    echo ""
    echo "Retrieving $ORG_B..."
    DIR_B=$(retrieve_org_to_temp "$ORG_B")
    [ -z "$DIR_B" ] && { rm -rf "$DIR_A"; exit 1; }

    print_drift_report "$DIR_A" "$DIR_B" "$ORG_A" "$ORG_B"

    rm -rf "$DIR_A" "$DIR_B"
    ;;

  branch-vs-branch)
    BRANCH_A=$2
    BRANCH_B=$3
    if [ -z "$BRANCH_A" ] || [ -z "$BRANCH_B" ]; then
      echo "ERROR: branch-vs-branch requires <branch-a> <branch-b>"
      echo "Usage: ./scripts/4-compare.sh branch-vs-branch <branch-a> <branch-b>"
      exit 1
    fi

    echo "Mode:         branch-vs-branch"
    echo "Branch A:     origin/$BRANCH_A"
    echo "Branch B:     origin/$BRANCH_B"

    echo ""
    echo "Fetching latest from remote..."
    git -C "$PROJECT_ROOT" fetch origin >/dev/null 2>&1

    echo ""
    echo "Snapshotting $BRANCH_A..."
    DIR_A=$(snapshot_branch_to_temp "origin/$BRANCH_A")
    [ -z "$DIR_A" ] && exit 1

    echo ""
    echo "Snapshotting $BRANCH_B..."
    DIR_B=$(snapshot_branch_to_temp "origin/$BRANCH_B")
    [ -z "$DIR_B" ] && { cleanup_worktree "$DIR_A"; exit 1; }

    print_drift_report "$DIR_A" "$DIR_B" "origin/$BRANCH_A" "origin/$BRANCH_B"

    cleanup_worktree "$DIR_A"
    cleanup_worktree "$DIR_B"
    ;;

  *)
    echo "ERROR: Unknown mode '$MODE'"
    echo ""
    echo "Usage:"
    echo "  ./scripts/4-compare.sh org-vs-branch <org>"
    echo "  ./scripts/4-compare.sh org-vs-org <org-a> <org-b>"
    echo "  ./scripts/4-compare.sh branch-vs-branch <branch-a> <branch-b>"
    exit 1
    ;;
esac

echo ""
echo "✓ Compare complete."
