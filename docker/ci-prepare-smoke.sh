#!/usr/bin/env bash
# Pre-seed the reviewed, immutable ClinGen reference bundle for the container smoke stack.
#
# The production image is code-only and `clingen-data-init` runs with `network_mode: none`,
# so it can never fetch data itself. This hook — the ONLY place the stack is allowed to reach
# the network — downloads the exact data release named in container-release.json and proves
# its identity against the digest committed there BEFORE the bundle is placed where the
# sidecar can read it.
#
# The committed digest is the trust root. The download host is not trusted: a tampered
# artifact fails this check and never reaches the stack.
#
# Contract (set by the router's reusable container-ci workflow):
#   GF_SMOKE_FIXTURE_DIR  directory to write fixtures into
#   GF_SMOKE_ENV_FILE     file to append bounded KEY=VALUE assignments to
set -euo pipefail

: "${GF_SMOKE_FIXTURE_DIR:?GF_SMOKE_FIXTURE_DIR is required}"
: "${GF_SMOKE_ENV_FILE:?GF_SMOKE_ENV_FILE is required}"

repository="${GITHUB_REPOSITORY:-berntpopp/clingen-link}"
config="$(dirname "$0")/../container-release.json"

release_tag="$(jq -er '.data.release_tag' "$config")"
identity_digest="$(jq -er '.data.digest' "$config")"
bundle_assignment="$(jq -er '.smoke_environment[] | select(startswith("CLINGEN_LINK_DATA_BUNDLE_SHA256="))' "$config")"
expected="${bundle_assignment#CLINGEN_LINK_DATA_BUNDLE_SHA256=}"
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || {
  echo "container-release.json smoke bundle digest is not a sha256 hex digest" >&2
  exit 1
}
seed_dir="$GF_SMOKE_FIXTURE_DIR/clingen-seed"
mkdir -p "$seed_dir"
bundle="$seed_dir/clingen.sqlite.zst"
manifest="$GF_SMOKE_FIXTURE_DIR/data-release-manifest.json"
base="https://github.com/${repository}/releases/download/${release_tag}"

curl -fsSL --proto '=https' --tlsv1.2 --max-time 600 -o "$bundle" "${base}/clingen.sqlite.zst"
curl -fsSL --proto '=https' --tlsv1.2 --max-time 120 -o "$manifest" \
  "${base}/data-release-manifest.json"

# Authenticity: the artifact must be exactly the one this commit reviewed.
echo "${expected}  ${bundle}" | sha256sum -c -

# The manifest is NOT a trust root — it is only a convenience carrier for the expanded-tree
# identity. A tampered manifest can make materialization fail; it can never make the sidecar
# accept a bundle whose bytes differ from the committed digest, which was just proven.
manifest_release="$(jq -er '.dataset.release' "$manifest")"
manifest_sha="$(jq -er '.artifact.sha256' "$manifest")"
test "$manifest_release" = "$release_tag"
test "$manifest_sha" = "$expected"

expanded="$(jq -er '.artifact.expanded_tree_sha256' "$manifest")"
schema_actual="$(jq -er '.schema.actual' "$manifest")"
schema_minimum="$(jq -er '.schema.minimum' "$manifest")"
schema_maximum="$(jq -er '.schema.maximum' "$manifest")"

{
  echo "CLINGEN_LINK_DATA_SEED_DIR=${seed_dir}"
  echo "CLINGEN_LINK_DATA_RELEASE_TAG=${release_tag}"
  echo "CLINGEN_LINK_DATA_IDENTITY_DIGEST=${identity_digest}"
  echo "CLINGEN_LINK_DATA_BUNDLE_SHA256=${expected}"
  echo "CLINGEN_LINK_DATA_EXPANDED_SHA256=${expanded}"
  echo "CLINGEN_LINK_DATA_SCHEMA_VERSION=${schema_actual}"
  echo "CLINGEN_LINK_DATA_SCHEMA_MINIMUM=${schema_minimum}"
  echo "CLINGEN_LINK_DATA_SCHEMA_MAXIMUM=${schema_maximum}"
} >> "$GF_SMOKE_ENV_FILE"

echo "prepared ${release_tag} bundle at ${bundle}"
