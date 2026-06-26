---
name: bump-version
description: Interactively bump the version of the Polyconnect integration and/or bridge add-on. Use this skill whenever the user wants to bump, release, or change the version number of any project component. Triggers on: "bump version", "new release", "increment version", "bump patch", "bump minor", "bump major", "release a new version", "update version number", or any variant involving version management. Always interactive — asks which component, what bump type, and confirms before applying.
---

# Bump Version

Interactively bump the version of Polyconnect project components.

## Workflow

### Step 1 — Show current versions

Run the status command to display current versions:

```bash
./scripts/bump-version.sh status
```

Present the output to the user so they can see what's currently deployed.

### Step 2 — Ask what to bump

Ask the user which component(s) to bump using the `question` tool:

- **Integration** — the HA custom component (`custom_components/polyconnect/manifest.json`)
- **Bridge** — the Supervisor add-on (`polyconnect_bridge/config.yaml` + `server.py`)
- **Both** — bump both components

### Step 3 — Ask bump type

For each selected component, ask the user what kind of bump they want:

- **patch** — bug fixes, no new features (X.Y.Z → X.Y.Z+1)
- **minor** — new features, backward compatible (X.Y.Z → X.Y+1.0)
- **major** — breaking changes (X.Y.Z → X+1.0.0)
- **Custom version** — let the user type an exact semver string

If the user picks "Custom version", ask them to provide the exact version number (must be valid semver: `X.Y.Z`).

### Step 4 — Confirm before applying

Show the user a summary of what will change:

```
Summary:
  integration: 2.0.0 → 2.0.1
  bridge:      2.0.3 → 2.0.4
```

Ask for confirmation before proceeding.

### Step 5 — Execute the bump

Run the appropriate bump command(s):

```bash
# Single component
./scripts/bump-version.sh integration patch
./scripts/bump-version.sh bridge 2.1.0

# Both
./scripts/bump-version.sh all minor
```

### Step 6 — Ask about git commit

After a successful bump, ask the user if they want to:

- **Commit & tag** — stage, commit with conventional message, and create git tag(s)
- **Commit only** — stage and commit, no tags
- **Skip** — leave changes uncommitted for manual review

If they choose to commit, run the appropriate git commands:

```bash
# Integration only
git add -A && git commit -m "chore(integration): bump to <version>"
git tag integration-v<version>

# Bridge only
git add -A && git commit -m "chore(bridge): bump to <version>"
git tag bridge-v<version>

# Both
git add -A && git commit -m "chore: bump integration=<ver> bridge=<ver>"
git tag integration-v<ver> && git tag bridge-v<ver>
```

### Step 7 — Ask about push

If the user committed, ask if they want to push:

- **Push with tags** — `git push && git push --tags`
- **Push without tags** — `git push`
- **Skip** — don't push yet

## Notes

- The bump script lives at `scripts/bump-version.sh`
- Each component has its own version — they can diverge
- When bumping "both" with a relative bump (patch/minor/major), each component bumps relative to its *own* current version
- Tags are namespaced: `integration-v2.0.1`, `bridge-v2.0.4`
- Always show the user the final state after bumping by running `./scripts/bump-version.sh status`
