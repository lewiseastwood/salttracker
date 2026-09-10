#!/usr/bin/env bash
# Open a refresh PR from the current branch. Routine PRs auto-merge so the
# dashboard on main updates. Needs-review PRs wait. No weekly email.
set -euo pipefail

if [[ ! -f data/staging/proposal.json ]]; then
  echo "ERROR: data/staging/proposal.json missing" >&2
  exit 1
fi

LABEL=$(python3 -c "import json; print(json.load(open('data/staging/proposal.json'))['label'])")
STAMP=$(python3 -c "import json; print(json.load(open('data/staging/proposal.json')).get('stamp','')[:10])")
TITLE="Refresh ${STAMP} (${LABEL})"
BODY_FILE=data/staging/CHANGE_REPORT.txt

gh label create routine --description "Weekly refresh; auto-merged onto the dashboard" --color "1B3A4B" 2>/dev/null || true
gh label create "needs-review" --description "Weekly refresh; wait for a person" --color "C45C26" 2>/dev/null || true

PR_URL=$(gh pr create --title "$TITLE" --body-file "$BODY_FILE" --label "$LABEL")
echo "$PR_URL"

if [[ "$LABEL" == "routine" ]]; then
  gh pr merge --squash --delete-branch --yes
  echo "Routine PR merged. Dashboard on main will pick up the new data."
else
  echo "Needs review — not merged. Dashboard is unchanged until you merge $PR_URL"
fi
