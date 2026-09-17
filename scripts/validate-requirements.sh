#!/usr/bin/env bash

set -u

# ============================================================
# ATC Requirement Traceability Validator
#
# Usage:
#
#   ./scripts/validate-requirements.sh radar
#   ./scripts/validate-requirements.sh tower
#   ./scripts/validate-requirements.sh command
#
# A requirement must have:
#
#   1. Requirement document
#   2. Entry in docs/requirements-index.md
#   3. Source implementation referencing the requirement ID
#   4. Automated test referencing the requirement ID
# ============================================================

# ------------------------------------------------------------
# Validate argument
# ------------------------------------------------------------

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <subsystem>"
    echo ""
    echo "Supported subsystems:"
    echo "  radar"
    echo "  tower"
    echo "  command"
    exit 1
fi

SUBSYSTEM="$1"

case "$SUBSYSTEM" in
    radar|tower|command)
        ;;
    *)
        echo "ERROR: Unknown subsystem: $SUBSYSTEM"
        echo ""
        echo "Supported subsystems:"
        echo "  radar"
        echo "  tower"
        echo "  command"
        exit 1
        ;;
esac

REQUIREMENTS_DIR="./requirements/$SUBSYSTEM"
SRC_DIR="./src/$SUBSYSTEM"
TESTS_DIR="./tests/$SUBSYSTEM"
REQUIREMENTS_INDEX="./docs/requirements-index.md"

errors=0
requirements_checked=0

echo "=========================================="
echo "ATC Requirement Traceability Validation"
echo "=========================================="
echo "Subsystem: $SUBSYSTEM"
echo "=========================================="

# ------------------------------------------------------------
# Validate directories and index
# ------------------------------------------------------------

for directory in "$REQUIREMENTS_DIR" "$SRC_DIR" "$TESTS_DIR"; do
    if [[ ! -d "$directory" ]]; then
        echo ""
        echo "ERROR: Required directory does not exist:"
        echo "       $directory"
        errors=$((errors + 1))
    fi
done

if [[ ! -f "$REQUIREMENTS_INDEX" ]]; then
    echo ""
    echo "ERROR: Requirements index does not exist:"
    echo "       $REQUIREMENTS_INDEX"
    exit 1
fi

if [[ $errors -gt 0 ]]; then
    exit 1
fi

# ------------------------------------------------------------
# Validate every requirement
# ------------------------------------------------------------

while IFS= read -r -d '' requirement_file; do
    filename=$(basename "$requirement_file")
    req_id=$(echo "$filename" | grep -oE 'REQ-[A-Z]+-[0-9]+')

    if [[ -z "$req_id" ]]; then
        echo ""
        echo "ERROR: Invalid requirement filename:"
        echo "       $requirement_file"
        errors=$((errors + 1))
        continue
    fi

    requirements_checked=$((requirements_checked + 1))

    echo ""
    echo "Checking: $req_id"
    echo "------------------------------------------"

    echo "  Requirement:"
    if [[ -f "$requirement_file" ]]; then
        echo "    PASS: $requirement_file"
    else
        echo "    FAIL: Requirement document does not exist"
        errors=$((errors + 1))
    fi

    echo "  Index:"
    index_matches=$(grep -E "^\|[[:space:]]*$req_id[[:space:]]*\|" "$REQUIREMENTS_INDEX" 2>/dev/null || true)

    if [[ -n "$index_matches" ]]; then
        echo "    PASS: $req_id exists in requirements-index.md"
        echo "          $index_matches"
    else
        echo "    FAIL: $req_id is missing from requirements-index.md"
        errors=$((errors + 1))
    fi

    echo "  Source:"
    source_matches=$(grep -ril \
        --include="*.ts" \
        --include="*.tsx" \
        --include="*.js" \
        --include="*.jsx" \
        --include="*.py" \
        --include="*.java" \
        --include="*.go" \
        --include="*.c" \
        --include="*.cpp" \
        --include="*.h" \
        --include="*.hpp" \
        "$req_id" \
        "$SRC_DIR" 2>/dev/null || true)

    if [[ -n "$source_matches" ]]; then
        echo "    PASS"
        while IFS= read -r file; do
            echo "          $file"
        done <<< "$source_matches"
    else
        echo "    FAIL: No source implementation references $req_id"
        errors=$((errors + 1))
    fi

    echo "  Tests:"
    test_matches=$(grep -ril \
        --include="*.ts" \
        --include="*.tsx" \
        --include="*.js" \
        --include="*.jsx" \
        --include="*.py" \
        "$req_id" \
        "$TESTS_DIR" 2>/dev/null || true)

    if [[ -n "$test_matches" ]]; then
        echo "    PASS"
        while IFS= read -r file; do
            echo "          $file"
        done <<< "$test_matches"
    else
        echo "    FAIL: No automated test references $req_id"
        errors=$((errors + 1))
    fi
done < <(find "$REQUIREMENTS_DIR" -type f -name '*.md' -print0)

if [[ $requirements_checked -eq 0 ]]; then
    echo ""
    echo "ERROR: No requirement files found in ./requirements/$SUBSYSTEM"
    errors=$((errors + 1))
fi

if [[ $errors -gt 0 ]]; then
    echo ""
    echo "Validation failed with $errors error(s)."
    exit 1
fi

echo ""
echo "Validation passed for $SUBSYSTEM."
