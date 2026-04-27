#!/usr/bin/env bash
# Conservatively remove the Envoy egress sidecar from a Helm chart or Kustomize tree.
# Writes changes in place and prints a unified diff to stdout for human review.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <dir>" >&2
  exit 64
fi

TARGET_DIR="$1"
if [[ ! -d "$TARGET_DIR" ]]; then
  echo "error: $TARGET_DIR is not a directory" >&2
  exit 64
fi

if ! command -v yq >/dev/null 2>&1; then
  echo "error: yq (v4+) is required" >&2
  exit 127
fi

shopt -s globstar nullglob
changed_any=0

for f in "$TARGET_DIR"/**/*.yaml "$TARGET_DIR"/**/*.yml; do
  # Only touch files that look like pod-spec carriers.
  if ! grep -qE '^(kind: (Deployment|StatefulSet|DaemonSet|Pod|Job|CronJob)\b|^\s*template:)' "$f"; then
    continue
  fi

  before="$(cat "$f")"
  tmp="$(mktemp)"
  cp "$f" "$tmp"

  # Remove envoy sidecar containers (by name).
  yq -i '
    (.spec.template.spec.containers, .spec.containers)?
      |= (select(. != null) | map(select(.name != "envoy" and .name != "envoy-sidecar")))
  ' "$tmp" 2>/dev/null || { echo "WARN: yq failed on $f, leaving unchanged" >&2; rm "$tmp"; continue; }

  # Remove iptables init container (by name).
  yq -i '
    (.spec.template.spec.initContainers, .spec.initContainers)?
      |= (select(. != null) | map(select(.name != "iptables-init" and .name != "envoy-iptables")))
  ' "$tmp" 2>/dev/null || true

  # Remove envoy-config volume mount references and the volume itself.
  yq -i '
    (.. | select(has("volumeMounts")?)).volumeMounts |=
      map(select(.name != "envoy-config" and .name != "envoy-certs"))
  ' "$tmp" 2>/dev/null || true
  yq -i '
    (.spec.template.spec.volumes, .spec.volumes)?
      |= (select(. != null) | map(select(.name != "envoy-config" and .name != "envoy-certs")))
  ' "$tmp" 2>/dev/null || true

  # Remove NET_ADMIN from any remaining init containers' capabilities.add list.
  yq -i '
    (.. | select(has("capabilities")?)).capabilities.add? |=
      (select(. != null) | map(select(. != "NET_ADMIN")))
  ' "$tmp" 2>/dev/null || true

  after="$(cat "$tmp")"
  if [[ "$before" != "$after" ]]; then
    mv "$tmp" "$f"
    echo "--- a/$f"
    echo "+++ b/$f"
    diff -u <(printf '%s\n' "$before") <(printf '%s\n' "$after") || true
    changed_any=1
  else
    rm "$tmp"
  fi
done

if [[ "$changed_any" -eq 0 ]]; then
  echo "No Envoy-related entries identified. If you expected changes, verify structure and names." >&2
fi
