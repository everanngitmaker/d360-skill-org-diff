# d360-org-diff

A Claude Code skill for detecting Salesforce Data 360 metadata drift between environments in a branch-per-org pipeline.

## What it does

Answers three distinct questions about metadata state across environments:

| Question | Mode |
|----------|------|
| Did anyone bypass devops and change an org directly? | `org-vs-branch` |
| What's deployed in one env but not promoted to the next yet? | `org-vs-org` |
| What's been merged to one branch but not the other? | `branch-vs-branch` |

## Usage

Invoke in Claude Code with phrases like:

- "check drift"
- "compare orgs"
- "what's different between dev and stage"
- "did anyone make manual changes to prod"
- "what's backlogged"

Claude will read `config/pipeline.config` for your org-to-branch mapping before running anything.

## Output

Every run produces:
- **Terminal summary** — drift counts by metadata category
- **HTML report** — saved to `reports/` and auto-opened in the browser, showing full drift grouped by type with expandable inline diffs

Reports are scoped to the manifest(s) in `manifests/` so only relevant data kits appear.

## Script

```bash
./scripts/4-compare.sh org-vs-branch <org>              # manual org changes
./scripts/4-compare.sh org-vs-org <org-a> <org-b>       # environment backlog
./scripts/4-compare.sh branch-vs-branch <branch-a> <b>  # git backlog
```

## Known limitations

- **UI deletions may not be detected** — when a field mapping is deleted in the Data Cloud UI, the metadata file often still comes back on retrieve due to a platform limitation. Verify deletions directly in Data Cloud Setup → Data Kits.
- **`<externalDataTranField>` false positives** — stripped automatically by `diff_report.py` before diffing.

## What's bundled

The compare tooling ships with this skill — no external repo needed:

- `scripts/4-compare.sh` — runs the diff (all three modes)
- `scripts/diff_report.py` — builds the HTML report; required by `4-compare.sh`
- `config/pipeline.config` — template defining `ORG_BRANCH_MAP` and `PROMOTION_ORDER`

Your project repo also needs a `manifests/` folder holding your exported Data Kit `package.xml` (it scopes the comparison), and an `sfdx-project.json` at the root.

## Requirements

- **Salesforce CLI** (`sf`) — authenticated to each org you compare (`sf org list`)
- **Git** — `org-vs-branch` and `branch-vs-branch` modes diff against branches
- **Python 3** — for `diff_report.py` (standard library only, no pip installs)

> This skill pairs with **[d360-deploy](https://github.com/everanngitmaker/d360-skill-deploy)**: when `3-deploy.sh` aborts on a real conflict, it points you to `4-compare.sh org-vs-branch` — the `org-vs-branch` mode here. Install both skills into the same project repo and the deploy → conflict → compare flow works end-to-end.

## Installation

Register this repo as a Claude Code plugin marketplace, then install the skill:

```bash
claude plugin marketplace add https://github.com/everanngitmaker/d360-skill-org-diff.git
claude plugin install d360-org-diff
```
