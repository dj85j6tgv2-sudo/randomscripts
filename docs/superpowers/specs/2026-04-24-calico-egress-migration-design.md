# Calico NetworkPolicy Egress Generator — Design

**Date:** 2026-04-24
**Status:** Draft, pending user review

## Problem

Apps in the Python-application Kubernetes cluster currently enforce egress allowlists via an Envoy sidecar + iptables init container. Envoy's L7 behavior has caused repeated breakage (Dagster gRPC HTTP/2 codec, OAuth2 mTLS client-cert stripping, ClickHouse HTTP streaming). We are replacing it with Calico OSS NetworkPolicy, which enforces at L3/L4 only. The source-of-truth `egress-allowlist.yaml` stays; a new generator emits Calico NetworkPolicy resources instead of Envoy config.

## Scope

**In scope:**
- New Python generator producing one NetworkPolicy per environment, per app.
- Hostname → IP resolution with audit file.
- Unit tests, daily CI refresh, migration helper script, README.
- Applies to the Python-application cluster. Dagster runs in a separate cluster with its own deployment; this generator is not used there.

**Out of scope:** Squid/HTTP proxies; iptables; touching or retiring the existing Envoy generator (separate PR per app); any L7 feature (hostname-in-Host-header, paths); Calico Enterprise features (FQDN rules, GlobalNetworkPolicy beyond OSS, DNS policies).

## Input schema (observed from current `egress-allowlist.yaml`)

Top-level `egress:` is a list of rules. Each rule:

| Field | Type | Notes |
|---|---|---|
| `destination` | str | Single hostname / IP / CIDR. |
| `destinations` | list[str] | Mutually exclusive with `destination`. |
| `domains` | list[str] | Treated identically to `destinations` by this generator. |
| `port` | int | Single port. |
| `port_range` | `{start:int, end:int}` | Emitted as Calico `"start:end"` string. |
| `protocol` | `http`\|`tcp`\|`grpc` | All map to TCP in Calico. `https` accepted if encountered. |
| `envs` | list[str] | Absent ⇒ all envs. Known envs: `dev`, `stg`, `prd`. |
| `tls` | `{cert, key, ca?}` | L7 only — ignored by generator, logged at INFO for audit. |
| `description` | str | Copied into a Calico rule annotation-style comment (as YAML comment preceding the rule). |

## Architecture

```
egress-allowlist.yaml  ──►  generator/generate.py  ──►  out/networkpolicy-<env>.yaml  (one per env)
                                    │                   out/resolved-ips.json
                                    └── socket.gethostbyname_ex()
```

Invocation:

```
python generator/generate.py \
  --allowlist egress-allowlist.yaml \
  --app <app-name> \
  --selector app=<app-name> \   # repeatable; default derived from --app
  --output-dir out/ \
  --envs dev,stg,prd            # default
```

## Components

**`load_allowlist(path) -> list[Rule]`**
Parses YAML, normalizes each entry into a `Rule` dataclass:
```python
@dataclass(frozen=True)
class Rule:
    destinations: tuple[str, ...]
    ports: tuple[int | tuple[int, int], ...]  # port or (start,end)
    envs: frozenset[str] | None  # None = all envs
    description: str | None
```
Collapses `destination`/`destinations`/`domains` → `destinations`. Collapses `port`/`port_range` → `ports`. Protocol is discarded after validation (all map to TCP).

**`classify(value) -> Literal["cidr","ip","hostname","wildcard"]`**
- Valid CIDR → `cidr`
- `ipaddress.ip_address()` parses → `ip`
- Contains `*` → `wildcard` (hard error)
- Otherwise → `hostname`

**`resolve(rules) -> tuple[dict[str, list[str]], list[str]]`**
Returns `(resolved, failed)`. For each hostname, calls `socket.gethostbyname_ex()`, sorts IPs, logs to stderr. Failures appended to `failed`, rule entry skipped. Wildcard hostnames raise `ConfigError` immediately (exit 1).

**`filter_by_env(rules, env) -> list[Rule]`**
Drops rules where `envs` is set and does not include `env`.

**`build_policy(app, env, rules, selector, resolved) -> dict`**
Produces a `projectcalico.org/v3` `NetworkPolicy`:
- `metadata.name`: `<app>-egress`
- `metadata.namespace`: `<app>-<env>`
- `spec.selector`: from CLI (`app == "<app-name>"` by default); multiple `--selector k=v` joined with `&&`
- `spec.types`: `[Egress]`
- `spec.egress`:
  1. Allow UDP/53 to any (CoreDNS).
  2. Allow TCP/53 to any (CoreDNS fallback).
  3. Per input rule: `action: Allow`, `protocol: TCP`, `destination: {nets: [...], ports: [...]}`.
     - `nets`: CIDRs preserved as-is; IPs emitted as `/32`; hostnames expanded to sorted `/32`s from `resolved`.
     - `ports`: int list for individual ports, `"start:end"` string for ranges.
  4. Final `action: Deny`.

**`write_outputs(out_dir, policies, resolved)`**
- `out/networkpolicy-<env>.yaml` per env.
- `out/resolved-ips.json` (keys + list values sorted, 2-space indent, trailing newline).

**Determinism:** Rules sorted by `(first-net-sorted, first-port, description)` before emit. `nets` list sorted lexicographically within each rule. `resolved-ips.json` keys sorted.

**Exit codes:** `0` clean · `2` unresolved hostnames (other outputs still written) · `1` config error (wildcard, unknown protocol, both `destination` and `destinations` set, etc.).

## Testing (`generator/test_generate.py`)

pytest cases (mocked `socket.gethostbyname_ex`):
1. Single IP → single `/32` rule.
2. CIDR preserved as-is.
3. Hostname → sorted `/32`s.
4. `port_range` → `"start:end"` string.
5. Multiple `port`s (via list input) → int list.
6. Protocols `tcp`/`http`/`https`/`grpc` all emit TCP.
7. `envs: [prd]` only present in prd output.
8. DNS failure → WARN, entry skipped, non-zero exit.
9. DNS rules + final Deny present in every policy.
10. Wildcard → `ConfigError`, exit 1.
11. Determinism: generate twice, byte-identical outputs.

## GitHub Actions (`.github/workflows/refresh-egress-policies.yml`)

Triggers: cron `0 3 * * *`, push to `main` touching `egress-allowlist.yaml`, `workflow_dispatch`.

Steps:
1. Checkout.
2. Setup Python 3.11, `pip install pyyaml pytest`.
3. Run generator for each app (v1: single app; multi-app loop left as TODO).
4. `git diff --exit-code out/` — if changed, commit with `chore: refresh egress IPs` and push.
5. If exit code 2 (unresolved hostnames), append list to `$GITHUB_STEP_SUMMARY` and fail job.
6. `# TODO: kubectl apply -f out/ per cluster` comment block.

## Migration helper (`scripts/remove-envoy-sidecar.sh`)

Uses `yq` (v4) for YAML-aware edits. For a given Helm chart or Kustomize dir:
- Remove containers named `envoy` or `envoy-sidecar`.
- Remove init containers named `iptables-init` (or containing `iptables` in the image).
- Remove volumes/volumeMounts referencing `envoy-config` configmap.
- Remove `NET_ADMIN` from capabilities on matched init containers.
- On ambiguity (multiple candidates, unrecognized structure): warn to stderr, leave file unchanged.
- Emit a unified diff of changes to stdout for review.

## README

Sections: what this is · how to add a destination · supported destination types (IP, CIDR, hostname; TCP only; port or range) · limitations (no FQDN/wildcard, no L7 — intentional) · debugging checklist (allowlist? resolved-ips.json? pod labels match selector?) · cross-cluster note (e.g. `*.svc.cluster.local` entries resolve to external IPs from this cluster's perspective; replace with LB IP if resolution fails from CI) · migration status table (seeded empty).

## Open decisions (locked)

1. `domains` treated as `destinations`.
2. Wildcards → hard error.
3. In-cluster selectors **not needed** — Dagster runs in a separate cluster; cross-cluster targets resolve like any other hostname.
4. Selector: `--selector key=value` (repeatable), default `app=<app>`.
5. `tls:` → INFO log, ignored.
6. Deterministic sort: `nets` lexicographic; rules by `(first-net, first-port, description)`.

## Repository layout after change

```
.
├── egress-allowlist.yaml
├── generator/
│   ├── generate.py
│   └── test_generate.py
├── out/
│   ├── networkpolicy-dev.yaml
│   ├── networkpolicy-stg.yaml
│   ├── networkpolicy-prd.yaml
│   └── resolved-ips.json
├── scripts/
│   └── remove-envoy-sidecar.sh
├── .github/workflows/
│   └── refresh-egress-policies.yml
└── README.md  (updated)
```
