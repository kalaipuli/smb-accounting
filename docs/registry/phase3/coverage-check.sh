#!/usr/bin/env bash
# Phase 3 task-structure coverage check — phase-start-protocol.md Step 2.
# Implements Checks 1, 3, 4 and 5. Checks 2 and 6 are walked by hand
# (see framework/spaces/phase3-structure.md action 4 reference notes) —
# Check 2 requires cross-referencing quoted phrases against source-file prose
# across multiple file formats (project-plan.md, architecture.md,
# scope-registry.md, ADRs); Check 6 has no Requires-bearing SCOPE-NN/FEAT-NN
# closing in Phase 3 to walk (see below).
#
# A check that passes because it found nothing to check is a failure — this
# script prints what it checked, not only failures.

set -u
FAIL=0
REG_DIR="docs/registry/phase3"
PLAN="framework/project/project-plan.md"
SCOPE_REG="framework/project/scope-registry.md"
SCOPE_MD="framework/project/scope.md"

echo "=== Check 1 — Deliverable completeness ==="

echo "-- Checking every DLVR-3-* tag in project-plan.md Phase 3 section appears in the phase-level tasks.md Cites column, or is marked Deferred --"
phase3_dlvrs=$(sed -n '/## Phase 3 — Reporting/,/^---$/p' "$PLAN" | grep -oE "DLVR-3-[0-9]+" | sort -u)
found_any=0
for tag in $phase3_dlvrs; do
  found_any=1
  if grep -q "$tag" "$REG_DIR/tasks.md"; then
    echo "OK: $tag cited in $REG_DIR/tasks.md"
  elif grep -q "$tag.*Deferred to Phase" "$REG_DIR/tasks.md"; then
    echo "OK: $tag explicitly Deferred in $REG_DIR/tasks.md"
  else
    echo "FAIL: $tag not cited in $REG_DIR/tasks.md and not marked Deferred"
    FAIL=1
  fi
done
if [ "$found_any" -eq 0 ]; then
  echo "FAIL: no DLVR-3-* tags found in project-plan.md Phase 3 section — denominator is empty, check cannot have run"
  FAIL=1
fi

echo "-- Checking every PHFEAT-NN row in the phase-level tasks.md has a registered scope-registry.md row with a non-blank parent tag --"
phfeats=$(grep -oE "PHFEAT-[0-9]+" "$REG_DIR/tasks.md" | sort -u)
found_any=0
for tag in $phfeats; do
  found_any=1
  row=$(grep -E "^\| $tag \|" "$SCOPE_REG")
  if [ -z "$row" ]; then
    echo "FAIL: $tag has no row in $SCOPE_REG"
    FAIL=1
    continue
  fi
  parent=$(echo "$row" | awk -F'|' '{gsub(/ /,"",$5); print $5}')
  if [ -z "$parent" ] || [ "$parent" = "—" ]; then
    echo "FAIL: $tag's parent tag in $SCOPE_REG is blank"
    FAIL=1
  else
    echo "OK: $tag registered, parent tag $parent"
  fi
done
if [ "$found_any" -eq 0 ]; then
  echo "FAIL: no PHFEAT-* tags found in $REG_DIR/tasks.md — denominator is empty, check cannot have run"
  FAIL=1
fi

echo ""
echo "=== Check 3 — No blank Cites cells ==="
checked=0
for f in "$REG_DIR"/feature*/tasks.md; do
  [ -f "$f" ] || continue
  while IFS= read -r line; do
    # only task rows: start with "| P3-F"
    case "$line" in
      "| P3-F"*)
        checked=$((checked+1))
        # Cites is the last populated column before the trailing pipe
        cites=$(echo "$line" | awk -F'|' '{print $(NF-1)}' | sed 's/^ *//;s/ *$//')
        if [ -z "$cites" ]; then
          echo "FAIL: blank Cites cell in $f: $line"
          FAIL=1
        fi
        ;;
    esac
  done < "$f"
done
echo "Checked $checked task rows across $(ls "$REG_DIR"/feature*/tasks.md | wc -l | tr -d ' ') feature file(s) for blank Cites cells."
if [ "$checked" -eq 0 ]; then
  echo "FAIL: no task rows found — denominator is empty, check cannot have run"
  FAIL=1
fi

echo ""
echo "=== Check 4 — No descoped citations ==="
insection=$(sed -n '/## 1. In Scope/,/## 3. Exclusions/p' "$SCOPE_MD")
cited_scopes=$(grep -rhoE "SCOPE-[0-9]+" "$REG_DIR"/ 2>/dev/null | sort -u)
if [ -z "$cited_scopes" ]; then
  echo "No SCOPE-NN tags cited anywhere in $REG_DIR/ — this phase's task files cite DLVR-*/PHFEAT-*/ADR-* directly, not SCOPE-* (their SCOPE-* ancestry is one hop up in project-plan.md). Nothing for this check to fail on; recorded rather than silently skipped."
else
  for tag in $cited_scopes; do
    if echo "$insection" | grep -q "$tag"; then
      echo "OK: $tag is an In Scope row of scope.md"
    else
      echo "FAIL: $tag is cited in $REG_DIR/ but is not an In Scope row of scope.md"
      FAIL=1
    fi
  done
fi

echo ""
echo "=== Check 5 — Obligation-to-task traceability ==="
echo "-- Cross-Cutting Consumption Obligations table, rows marked satisfied --"
satisfied_rows=$(grep -E "^\|.*\| DLVR-.* \|.*satisfied" "$PLAN")
found_any=0
while IFS= read -r row; do
  [ -z "$row" ] && continue
  found_any=1
  phrase=$(echo "$row" | awk -F'|' '{print $2}' | sed 's/^ *//;s/ *$//')
  consumers=$(echo "$row" | awk -F'|' '{print $4}')
  # only check consumers that are Phase 3 (DLVR-3-*) — this script is scoped to Phase 3
  if echo "$consumers" | grep -q "DLVR-3-"; then
    if grep -rF "$phrase" "$REG_DIR"/feature*/tasks.md > /dev/null 2>&1; then
      echo "OK: obligation phrase found in a Phase 3 task row: \"$phrase\""
    else
      echo "FAIL: obligation phrase not found in any Phase 3 task row: \"$phrase\""
      FAIL=1
    fi
  else
    echo "SKIP (no Phase 3 consumer listed yet): \"$phrase\""
  fi
done <<< "$satisfied_rows"
if [ "$found_any" -eq 0 ]; then
  echo "FAIL: no satisfied obligation rows found in $PLAN — denominator is empty, check cannot have run"
  FAIL=1
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "=== ALL CHECKS PASSED ==="
  exit 0
else
  echo "=== FAIL — see FAIL lines above ==="
  exit 1
fi
