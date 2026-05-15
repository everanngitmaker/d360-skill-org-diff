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

The `4-compare.sh` script lives in [d360-deploy-cli-pipeline](https://github.com/everanngitmaker/d360-deploy-cli-pipeline) — use that repo to set up or copy the pipeline into your project.

```bash
./scripts/4-compare.sh org-vs-branch <org>              # manual org changes
./scripts/4-compare.sh org-vs-org <org-a> <org-b>       # environment backlog
./scripts/4-compare.sh branch-vs-branch <branch-a> <b>  # git backlog
```

## Known limitations

- **UI deletions may not be detected** — when a field mapping is deleted in the Data Cloud UI, the metadata file often still comes back on retrieve due to a platform limitation. Verify deletions directly in Data Cloud Setup → Data Kits.
- **`<externalDataTranField>` false positives** — stripped automatically by `diff_report.py` before diffing.

## Installation

This skill is part of the [my-skills](https://github.com/everanngitmaker) plugin collection. Copy the skill directory into your Claude Code skills folder:

```bash
cp -r d360-org-diff ~/.claude/plugins/marketplaces/my-skills/plugins/my-skills/skills/
```
