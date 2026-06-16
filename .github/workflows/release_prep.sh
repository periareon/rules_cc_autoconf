#!/usr/bin/env bash
# Build the rules_cc_autoconf release archive and emit release notes.
# Invoked by bazel-contrib/.github/.github/workflows/release_ruleset.yaml
# at the hardcoded path `.github/workflows/release_prep.sh`.
#
# Args:
#   $1: tag name (e.g. 0.1.0). Must match VERSION in version.bzl.
#
# Side effects:
#   Writes rules_cc_autoconf-${TAG}.tar.gz to the current directory. This
#   filename must match the `release_files` glob in release.yaml.
#
# Output:
#   Release notes to stdout. The release_ruleset workflow redirects
#   stdout into release_notes.txt for the GitHub release body. All
#   other noise (tar, sed, version checks) goes to stderr.

set -euo pipefail

# Redirect all stdout to stderr; keep fd 3 for the final release-notes write.
exec 3>&1 1>&2

TAG="${1:?tag_name required}"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"

# Validate the tag matches version.bzl. release.yaml's `validation` job
# also guards this, but checking again here means a manual invocation
# of this script can't drift.
ON_DISK_VERSION="$(grep 'VERSION =' "${WORKSPACE}/version.bzl" | sed 's/VERSION = "//' | sed 's/"//')"
if [[ "${ON_DISK_VERSION}" != "${TAG}" ]]; then
    echo "ERROR: tag ${TAG} does not match version.bzl VERSION=${ON_DISK_VERSION}"
    exit 1
fi

# Build the source archive. Exclude .git (history isn't needed by
# consumers) and .github (release infrastructure isn't part of the
# ruleset). The on-disk filename matches the URL in
# `.bcr/source.template.json` — the SLSA attestation subject must
# equal the published asset name.
#
# Stage to a temp path *outside* the workspace before moving into cwd:
# writing the archive into the same directory tar is reading from
# updates that directory's mtime mid-read, which GNU tar surfaces as
# `tar: .: file changed as we read it` and aborts with non-zero.
ARCHIVE="rules_cc_autoconf-${TAG}.tar.gz"
STAGING="$(mktemp -d)/${ARCHIVE}"
trap 'rm -rf "$(dirname "${STAGING}")"' EXIT
tar -czf "${STAGING}" \
    --exclude=".git" \
    --exclude=".github" \
    -C "${WORKSPACE}" .
mv "${STAGING}" "${ARCHIVE}"

# Render release notes by substituting {version} into the template.
# release_notes.template lives next to this script.
NOTES="$(mktemp)"
sed "s#{version}#${TAG}#g" \
    "${WORKSPACE}/.github/release_notes.template" > "${NOTES}"

# Emit the rendered notes on the original stdout (fd 3) — that's the
# only thing the reusable workflow captures into release_notes.txt.
cat "${NOTES}" >&3
