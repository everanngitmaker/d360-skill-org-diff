---
name: d360-org-diff
description: Detect Data Cloud metadata drift between environments in the d360devops2 pipeline. Use this skill whenever the user wants to compare orgs against each other, compare an org against its git branch, or compare two git branches to see backlogged changes. Trigger on phrases like "check drift", "compare orgs", "what's different between dev and stage", "did anyone make manual changes to prod", "what's backlogged", "compare mysdo-dev to mysdo-stage", or any request to understand metadata differences between environments.
type: reference
---

# D360 Org Diff

A skill for detecting metadata drift in the d360devops2 pipeline. Three distinct comparisons answer three different questions — pick the right one before running anything.

## When to use which mode

Identify which question the user is asking, then run the matching script mode.

| User's question | Mode | Script |
|---|---|---|
| "Did anyone bypass devops and change the org directly?" | **org-vs-branch** | `./scripts/4-compare.sh org-vs-branch <org>` |
| "What's deployed in one env that isn't in the other yet?" | **org-vs-org** | `./scripts/4-compare.sh org-vs-org <org-a> <org-b>` |
| "What's been merged to one branch but not the other?" | **branch-vs-branch** | `./scripts/4-compare.sh branch-vs-branch <branch-a> <branch-b>` |

If the user's intent is ambiguous, ask which question they're trying to answer. For example, if they say "compare dev and stage" they could mean either org-vs-org (live comparison) or branch-vs-branch (git comparison) — these often give different answers.

## How to orient yourself

Before running the script:
1. Check `config/pipeline.config` for `ORG_BRANCH_MAP` to see the org→branch mapping for this project. Every project has different org aliases.
2. Confirm at least one manifest exists in `manifests/`. The manifest scopes the comparison — it determines which data kits are included in the retrieve.
3. Confirm all relevant orgs are authenticated: `sf org list`.

## Running the script

### Mode 1: org-vs-branch — detect manual org changes

This retrieves the current state of the org and diffs it against the org's mapped env branch on GitHub. Use this to find changes that were made directly in the org UI, bypassing the devops pipeline.

```bash
./scripts/4-compare.sh org-vs-branch <org>
```

Example:
```bash
./scripts/4-compare.sh org-vs-branch mysdo-dev
```

### Mode 2: org-vs-org — find environment backlogs

This retrieves metadata from two orgs and diffs them. Use this to see which changes are in one environment but haven't been promoted to the next yet. Reflects actual deployed state in each org.

```bash
./scripts/4-compare.sh org-vs-org <org-a> <org-b>
```

Example:
```bash
./scripts/4-compare.sh org-vs-org mysdo-dev mysdo-stage
```

### Mode 3: branch-vs-branch — find git backlogs

This diffs two git branches directly. No SF CLI retrieve needed. Use this to see what's been merged to one branch but not the other — i.e. what's waiting to be promoted in git, regardless of whether it's been deployed to any org yet.

```bash
./scripts/4-compare.sh branch-vs-branch <branch-a> <branch-b>
```

Example:
```bash
./scripts/4-compare.sh branch-vs-branch dev stage
```

## How the output is organized

Every run produces two outputs:

1. **Terminal summary** — quick counts of drift by category.
2. **HTML report** — saved to `reports/drift-<left>_vs_<right>-<timestamp>.html` and auto-opened in the browser. Shows the full drift grouped by metadata type, with expandable inline diffs for modified files.

The report is **scoped to the manifest(s) in `manifests/`**. Out-of-scope metadata (e.g. unrelated data kits that happen to live on the branch) is filtered out, so the user only sees what's actually relevant to this comparison. The `reports/` directory is gitignored.

The report shows three categories:
- **Only in \<left side\>** — files present on the left but not the right
- **Only in \<right side\>** — files present on the right but not the left
- **Modified on both sides** — files that exist in both but differ in content (with inline unified diffs)

If the report says "No drift detected", the two sides match for every in-scope file.

## Known false positive: `<externalDataTranField>` / `<externalDataTranObject>` in deployed orgs

When comparing a deployed org (stage, prod) against its branch, `ExtDataTranObjectTemplate` files may appear as modified even though nothing changed. This is because Salesforce returns `<externalDataTranField>` and `<externalDataTranObject>` on retrieve when metadata was **authored natively** in an org (e.g. dev), but silently drops both elements when the same metadata is **received via deployment**. Both elements duplicate `<externalName>` and are functionally redundant.

`diff_report.py` strips these elements before hashing and diffing, so they should not appear in reports. If they do, the version of `diff_report.py` in the repo may be out of date.

## Known gap: destructive changes in org UI

When someone manually deletes a field mapping in the Data Cloud UI, the mapping's metadata file often still comes back on the next retrieve. This is because Data Cloud's published data kit state continues to reference deleted components. As a result:

- **org-vs-branch mode may NOT detect UI deletions** — the diff will show no drift even though the mapping was deleted in the org.
- **To confirm a deletion**, check the Data Cloud Setup UI directly: Data Kits → \<your kit\> → field mappings.

This is a Data Cloud platform limitation, not a bug in the script. Additive changes, modified mappings, and new metadata are all detected correctly.

## Interpreting the manifest scope

The comparison is scoped by the manifest(s) in `manifests/`. Only metadata types and members listed in the manifest are retrieved and diffed. If the user wants to compare across multiple data kits, make sure all their manifests are in `manifests/` before running.

## Troubleshooting

**"No manifest files found"** — Drop a `package.xml` into `manifests/` before running.

**"No branch mapping for org"** — Add an entry to `ORG_BRANCH_MAP` in `config/pipeline.config`, e.g. `mysdo-dev:dev`.

**"Retrieve failed"** — Check `sf org list` to confirm the org is authenticated. Check that the manifest's members exist in the target org.

**Script runs but diff shows everything as drift** — The manifests folder may have been wiped by a prior interrupted run. Re-add the manifest and re-run.
