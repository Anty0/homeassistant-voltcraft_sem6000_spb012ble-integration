---
allowed-tools: Bash(grep:*), Bash(git describe:*), Bash(git branch:*), Bash(git log:*), Bash(git diff:*), Bash(git status:*), Bash(git tag:*), Bash(git add:*), Bash(git commit:*), Bash(git reset:*), Bash(gh repo view:*), Bash(gh run:*), Bash(gh run list:*), Bash(gh run watch:*), Bash(gh run rerun:*), Bash(gh release delete:*), Bash(gh release create:*), Edit, Read
description: Release a new version - bump version, commit, tag, push, create GitHub release
argument-hint: "[major|minor|patch]"
---

# Release

## Current State

- Current version in manifest.json: !`grep '"version"' custom_components/voltcraft_sem6000_spb012ble/manifest.json`
- Latest git tag: !`git describe --tags --abbrev=0`
- Current branch: !`git branch --show-current`
- GitHub repo: !`gh repo view --json nameWithOwner -q .nameWithOwner`

## Instructions

Follow these steps precisely to create a new release.

### Step 1: Determine version bump

If `$ARGUMENTS` is one of `major`, `minor`, or `patch`, use that bump type.

Otherwise, analyze the commits since the last tag and determine the appropriate semantic version bump:
- **patch** (X.Y.Z → X.Y.Z+1): bug fixes, chores, docs, minor tweaks
- **minor** (X.Y.Z → X.Y+1.0): new features, API changes, user-facing improvements
- **major** (X.Y.Z → X+1.0.0): breaking changes, major reworks — always confirm with user before bumping major

Present the proposed new version to the user and ask them to confirm using `AskUserQuestion`.

### Step 2: Summarize changes

Write concise, user-facing bullet points for each meaningful change since the last tag. Use plain language, not raw commit messages. These will go into the GitHub release body.

### Step 3: Execute the release

Do all of the following in order:

1. Edit `custom_components/voltcraft_sem6000_spb012ble/manifest.json` to update the `version` field to the new version
2. Stage and commit:
   ```
   git add custom_components/voltcraft_sem6000_spb012ble/manifest.json
   git commit -m "release X.Y.Z"
   ```
3. Tag: `git tag X.Y.Z`
4. Push: `git push && git push --tags`

### Step 4: Create GitHub release

Create the release on GitHub. The title format is `X.Y.Z`. The body format is:

```
Changes:
- bullet point 1
- bullet point 2

**Full Changelog**: https://github.com/OWNER/REPO/compare/OLD...NEW
```

Use `gh release create` with `--title` and `--notes` flags. Pass the notes via a HEREDOC for correct formatting.

### Step 5: Wait for CI

After the release is pushed, watch GitHub Actions to make sure CI passes:

1. Wait a moment for workflows to trigger, then use `gh run list --limit 5` to find the runs triggered by the push
2. Use `gh run watch <run-id>` to monitor each workflow run (there are three: lint, HACS validate, hassfest)

If all checks pass, report success with a link to the release.

If any check fails, explain what happened and recommend one of these recovery strategies:
- **Flaky/random failure**: Rerun with `gh run rerun <run-id>`
- **Real failure**: We need to scrap the release and fix the issue:
  1. Delete the GitHub release: `gh release delete X.Y.Z --yes`
  2. Delete the remote tag: `git push --delete origin X.Y.Z`
  3. Delete the local tag: `git tag -d X.Y.Z`
  4. Remove the release commit: `git reset --hard HEAD~1 && git push --force`
  5. Fix the issue, then re-release with the same version
