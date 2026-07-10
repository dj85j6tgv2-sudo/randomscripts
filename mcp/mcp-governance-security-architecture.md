# MCP Governance and Security Architecture

**Version:** 1.0 (draft for review)
**Scope:** Federated MCP ecosystem over ClickHouse Silver databases, extensible to PostgreSQL, Oracle, REST APIs and other sources.
**Status:** Target architecture

---

## 1. Purpose

This document defines the target architecture for governing and securing a decentralized ecosystem of MCP servers. Each business or technical team builds, hosts and operates its own FastMCP server for a specific business use case. The central platform provides the identity model, the authorization engine, the enforcement libraries, the audit pipeline and the certification process, so that the secure implementation is the default and easiest implementation.

The model is federated: **teams own their MCPs and their business rules; the platform owns the security machinery those rules run on.**

---

## 2. Architecture Principles

1. **APIGEE authenticates; the platform authorizes.** APIGEE delivers a verified username (human UID or service account) and nothing more. All roles, groups, entitlements, agent grants and data policies live in the central Policy & Entitlement Service (PES).
2. **Every tool invocation is individually authorized.** Reaching the MCP endpoint grants nothing. Discovery authorization and execution authorization are separate checks.
3. **Effective access = Agent ∩ User ∩ Data.** A request is permitted only when the agent grant, the user/service-account entitlement, and the data policy all allow it.
4. **Authorization returns obligations, not just permit/deny.** Row predicates, column projections, masking rules and limits are returned by the PES and applied mechanically by shared enforcement code.
5. **Tool authors never assemble SQL.** All queries go through the platform data adapter, which injects mandatory predicates and projections structurally. There is no casual raw-SQL escape hatch.
6. **Client filters are suggestions; obligation predicates are law.** A filter supplied by an agent or user can only narrow results, never widen them.
7. **Defense in depth.** Application-level enforcement is backed by database-level enforcement (secured views, least-privilege grants) wherever the data is sensitive.
8. **Fail closed.** If the PES is unreachable or a decision cannot be obtained, the request is denied.
9. **Everything important is traceable.** One correlation ID follows the request across APIGEE, the MCP, the PES and the database. Audit events are emitted by both the MCP middleware and the PES.
10. **Technology-agnostic core.** The identity model, decision contract, obligations format and audit schema are database-independent. Only thin adapters are database-specific.

---

## 3. High-Level Architecture

```
                 ┌────────────────────────────────────────────────┐
                 │              Central Platform                  │
                 │                                                │
                 │  ┌──────────────────────────────────────────┐  │
 Admin UI ───────┼─▶│  Policy & Entitlement Service (PES)      │  │
 (teams manage   │  │  - Admin API (federated management)      │  │
  their MCPs)    │  │  - Decision API (/authorize)             │  │
                 │  │  - Postgres policy database              │  │
                 │  └──────────────┬───────────────────────────┘  │
                 │                 │ decision logs                │
                 │  ┌──────────────▼───────────────────────────┐  │
                 │  │  Audit & Observability Pipeline          │  │
                 │  │  (OTel traces, audit events, dashboards) │  │
                 │  └──────────────────────────────────────────┘  │
                 └────────────────────────────────────────────────┘
                            ▲                    ▲
                   /authorize (sync,             │ audit events,
                   cached, fail-closed)          │ traces
                            │                    │
 User / Agent ──▶ APIGEE ──▶│ MCP Server (team-owned, FastMCP)    │
 (OAuth PKCE,   (authn +    │  ┌───────────────────────────────┐  │
  mTLS, SA flow) routing)   └─▶│ Governance Middleware (shared)│  │
                               │  pre-hook → tool → post-hook  │  │
                               │  Data Adapter (shared)        │  │
                               └──────────────┬────────────────┘  │
                                              │ secured queries
                                              ▼
                               ClickHouse / PostgreSQL / Oracle / APIs
```

Components:

| Component | Owner | Role |
|---|---|---|
| APIGEE | Central | Authentication (mTLS, OAuth PKCE, SA flow), transport trust, routing, per-agent client credentials |
| Policy & Entitlement Service (PES) | Central (engine) / Teams (content) | Stores identities, roles, entitlements, agent grants, tool permissions, data policies; serves authorization decisions with obligations |
| Governance Middleware | Central | Shared FastMCP library: identity extraction, pre/post hooks, `@authorized_tool` decorator, decision caching, audit emission |
| Data Adapter | Central (interface + adapters) | Structurally injects mandatory predicates, projections, masking, limits; per-database implementations |
| MCP Servers | Teams | Business tools, use-case definition, hosting, lifecycle, incident response |
| Audit & Observability Pipeline | Central | Trace correlation, audit storage, dashboards, alerting, anomaly detection |
| MCP Registry & Certification | Central | Inventory of MCPs, compliance gate before APIGEE routes traffic |

---

## 4. Identity and Authentication

### 4.1 What APIGEE provides

APIGEE authenticates the caller and forwards the request with a **verified username only** — the human UID or the service-account identifier. The MCP must additionally verify that the request genuinely arrived via APIGEE (mTLS between APIGEE and the MCP, or a network path reachable only from APIGEE). Identity fields supplied directly by the client (headers, request body) are never trusted.

### 4.2 Agent identity

Because APIGEE does not carry an agent claim, agent identity is established through **per-agent APIGEE client credentials**: each AI agent or client application is registered as its own APIGEE app, so the client identifier arriving with the request is transport-verified. The mapping `apigee_client_id → agent_id` is maintained in the PES.

An `agent_id` supplied in the request payload is never authoritative. If a team cannot provision per-agent credentials, the fallback is inference from the APIGEE route/app registration — never from client-supplied fields.

### 4.3 IdentityContext

The governance middleware builds a single `IdentityContext` per request:

```python
@dataclass(frozen=True)
class IdentityContext:
    username: str            # verified human UID or SA id (from APIGEE)
    principal_type: str      # "human" | "service_account"
    agent_id: str            # resolved from APIGEE client id
    mcp_id: str              # this MCP's registry id
    trace_id: str            # correlation id (propagated or generated)
    auth_method: str         # "oauth_pkce" | "mtls" | "sa_flow"
    # populated after the PES decision:
    roles: tuple[str, ...]
    entitlements: Mapping[str, Any]
```

This object is the only source of identity for authorization, query enforcement and audit. Tool code receives it read-only.

---

## 5. Policy & Entitlement Service (PES)

The PES is the central system of record for authorization. It has three faces: a Postgres policy database, an Admin API + UI for federated management, and a Decision API on the request path.

### 5.1 Policy database schema (core tables)

```sql
-- Identities ---------------------------------------------------------------
CREATE TABLE principals (
    principal_id     text PRIMARY KEY,          -- UID or SA name (APIGEE username)
    principal_type   text NOT NULL CHECK (principal_type IN ('human','service_account')),
    display_name     text,
    org_unit         text,
    status           text NOT NULL DEFAULT 'active',
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agents (
    agent_id           text PRIMARY KEY,        -- logical agent identity
    apigee_client_id   text UNIQUE NOT NULL,    -- transport-verified mapping
    description        text,
    owning_team        text NOT NULL,
    status             text NOT NULL DEFAULT 'active'
);

-- Roles & entitlements -------------------------------------------------------
CREATE TABLE roles (
    role_id      text PRIMARY KEY,              -- e.g. 'regional_manager'
    scope        text NOT NULL,                 -- 'global' or an mcp_id (federation boundary)
    description  text
);

CREATE TABLE principal_roles (
    principal_id text REFERENCES principals,
    role_id      text REFERENCES roles,
    granted_by   text NOT NULL,
    granted_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz,
    PRIMARY KEY (principal_id, role_id)
);

-- Attribute entitlements: the values that feed row predicates
CREATE TABLE entitlements (
    principal_id text REFERENCES principals,
    attribute    text NOT NULL,                 -- 'authorized_regions', 'business_unit', ...
    value        jsonb NOT NULL,                -- ["FR-IDF","FR-NOR"] / "BU-RETAIL"
    granted_by   text NOT NULL,
    expires_at   timestamptz,
    PRIMARY KEY (principal_id, attribute)
);

-- MCPs, tools, grants ---------------------------------------------------------
CREATE TABLE mcps (
    mcp_id        text PRIMARY KEY,
    owning_team   text NOT NULL,
    description   text,
    status        text NOT NULL DEFAULT 'pending_certification',
    version       text
);

CREATE TABLE tools (
    mcp_id       text REFERENCES mcps,
    tool_name    text NOT NULL,
    permission   text NOT NULL,                 -- e.g. 'sales.read'
    resource     text NOT NULL,                 -- logical resource, e.g. 'sales'
    description  text,
    PRIMARY KEY (mcp_id, tool_name)
);

-- Layer 1: which agents may discover/execute which tools
CREATE TABLE agent_tool_grants (
    agent_id   text REFERENCES agents,
    mcp_id     text NOT NULL,
    tool_name  text NOT NULL,
    can_discover boolean NOT NULL DEFAULT true,
    can_execute  boolean NOT NULL DEFAULT true,
    FOREIGN KEY (mcp_id, tool_name) REFERENCES tools,
    PRIMARY KEY (agent_id, mcp_id, tool_name)
);

-- Layer 2: which roles may discover/execute which tools
CREATE TABLE role_tool_grants (
    role_id    text REFERENCES roles,
    mcp_id     text NOT NULL,
    tool_name  text NOT NULL,
    can_discover boolean NOT NULL DEFAULT true,
    can_execute  boolean NOT NULL DEFAULT true,
    FOREIGN KEY (mcp_id, tool_name) REFERENCES tools,
    PRIMARY KEY (role_id, mcp_id, tool_name)
);

-- Layer 3: data policies producing obligations
CREATE TABLE data_policies (
    policy_id     bigserial PRIMARY KEY,
    mcp_id        text NOT NULL,
    resource      text NOT NULL,                -- matches tools.resource
    applies_to    text NOT NULL,                -- role_id or '*'
    row_predicate text,                         -- 'region_id IN ({authorized_regions})'
    predicate_bindings jsonb,                   -- {"authorized_regions": "entitlement:authorized_regions"}
    allowed_columns text[],                     -- projection whitelist
    masked_columns  jsonb,                      -- {"customer_email": "redact"}
    max_rows      integer,
    aggregation_only boolean NOT NULL DEFAULT false,
    export_allowed   boolean NOT NULL DEFAULT false
);

-- Audit of policy administration itself
CREATE TABLE policy_audit (
    id          bigserial PRIMARY KEY,
    actor       text NOT NULL,
    action      text NOT NULL,
    target      jsonb NOT NULL,
    at          timestamptz NOT NULL DEFAULT now()
);
```

### 5.2 Federated administration

The Admin UI enforces the ownership split **inside the PES itself** (Postgres RLS on the policy tables, keyed by `owning_team`):

- The central team manages: `principals`, global `roles`, `agents`, the PES schema, and cross-cutting policies.
- Each MCP-owning team manages only rows where `mcp_id` belongs to its MCPs: its tools, its role/agent tool grants, its data policies, its MCP-scoped roles.
- Every administrative change is written to `policy_audit` and requires a second reviewer for grants on resources flagged sensitive (four-eyes principle).
- Grants support expiry (`expires_at`) and the UI drives periodic access reviews per team.

### 5.3 Decision API

`POST /authorize` — called by the MCP middleware before every tool execution (and in batch for discovery filtering).

Request:

```json
{
  "username": "u123456",
  "agent_id": "agent:reporting-assistant",
  "mcp_id": "sales-reporting",
  "tool": "regional_sales",
  "action": "execute",              // or "discover"
  "params_fingerprint": "sha256:…", // shape of supplied params, no values
  "trace_id": "abc-123"
}
```

Response:

```json
{
  "decision": "permit",
  "principal_type": "human",
  "roles": ["regional_manager"],
  "obligations": {
    "row_predicates": [
      {
        "resource": "sales",
        "predicate": "region_id IN ({authorized_regions})",
        "bindings": { "authorized_regions": ["FR-IDF", "FR-NOR"] }
      }
    ],
    "column_projection": ["order_id", "amount", "region_id", "period"],
    "masking": [ { "column": "customer_email", "strategy": "redact" } ],
    "max_rows": 10000,
    "aggregation_only": false,
    "export_allowed": false
  },
  "decision_id": "dec-9f3…",
  "ttl_seconds": 60
}
```

Decision logic: `permit` only if the agent grant, the principal's role grant and a matching data policy all allow the action (**Agent ∩ User ∩ Data**). Obligations are the union of all applicable data policies for the principal's roles, resolved against the principal's `entitlements` rows. Binding values are resolved server-side by the PES — the MCP never computes entitlements itself.

Operational requirements:

- **Availability:** ≥ 2 replicas behind a VIP; the PES is on the critical path.
- **Latency:** single-digit-ms decisions; policy tables are small and hot.
- **Caching:** middleware caches decisions per `(username, agent_id, mcp_id, tool)` for `ttl_seconds` (30–60 s), obligations included. Revocations propagate within the TTL.
- **Fail closed:** timeout or error ⇒ deny, with a distinct audit event (`decision: "deny_unavailable"`).
- **Decision logging:** every decision (permit and deny) is logged in the PES with `decision_id`, and the same `decision_id` appears in the MCP's audit event, giving two correlated records per request.

---

## 6. Governance Middleware (shared FastMCP library)

The central team ships one library that every MCP integrates. Integration is a few lines; everything below is mandatory and non-bypassable.

```python
mcp = GovernedFastMCP(
    name="sales-reporting",
    identity=ApigeeIdentityValidator(...),   # verifies APIGEE origin, extracts username + client id
    pes=PolicyClient(base_url=..., cache_ttl=60, fail_mode="closed"),
    audit=AuditEmitter(pipeline=...),        # OTel + audit events
)

@mcp.authorized_tool(permission="sales.read", resource="sales")
async def regional_sales(ctx: IdentityContext, region: str, period: str) -> Result:
    return await ctx.db.select(
        table="sales",
        columns=["order_id", "amount", "customer_email", "region_id"],
        where={"region_id": region, "period": period},
    )
```

Request pipeline (pre-hook → tool → post-hook):

1. **Verify transport origin** — request must arrive from APIGEE (mTLS / network path). Otherwise: reject.
2. **Extract identity** — username + APIGEE client id → resolve `agent_id` → build `IdentityContext`.
3. **Pre-execution authorization** — call PES `/authorize` (or cache hit). Deny ⇒ structured error, audit event, stop.
4. **Bind obligations to the adapter** — `ctx.db` is constructed for this request with the obligations baked in.
5. **Execute the tool** — tool code declares intent through the adapter; it cannot see or modify obligations.
6. **Post-execution filtering** — response-schema validation, column projection re-check, masking/redaction, row-count and export limits, defense-in-depth on nested/JSON fields.
7. **Audit + trace** — emit the audit event (see §10) and close the OTel span, propagating `trace_id` end to end.

The decorator also registers the tool's `permission` and `resource` with the registry at startup; an undecorated tool fails CI (see §12).

---

## 7. Query Enforcement and the Data Adapter

### 7.1 The rule

**Tool authors never build SQL strings.** They call the adapter's typed interface (`select`, `aggregate`, …). The adapter merges, at execution time:

1. **Mandatory row predicates** from obligations — parameterized, never string-interpolated. A client-supplied filter can only add restriction on top; if the agent requests `region='DE-BER'` and the user is entitled to French regions only, the intersection is empty and zero rows return.
2. **Column projection** — requested columns ∩ authorized columns (drop or reject, per policy).
3. **Limits** — `max_rows`, execution timeouts, aggregation-only enforcement.

Generated query for a regional manager:

```sql
SELECT order_id, amount, region_id
FROM sales
WHERE region_id = {p1:String} AND period = {p2:String}
  AND region_id IN ({auth_regions:Array(String)})   -- injected obligation
LIMIT 10000
```

### 7.2 ClickHouse mechanisms

Native `CREATE ROW POLICY` binds to ClickHouse users/roles. Since MCPs connect through a per-MCP service account, ClickHouse cannot distinguish end users — native row policies alone are insufficient. Two mechanisms are standardized:

**Mechanism A — `additional_table_filters` (general-purpose).** The adapter attaches obligation predicates as a per-query setting:

```python
await ch.query(
    sql,
    parameters={"p1": region, "p2": period},
    settings={"additional_table_filters": {"sales": "region_id IN ('FR-IDF','FR-NOR')"}},
)
```

The filter applies to every reference to the table in the query, including subqueries and JOINs — coverage that WHERE-clause injection can miss. No schema changes required.

**Mechanism B — parameterized views (sensitive tables).** Enforcement pushed into the database:

```sql
CREATE VIEW sales_secure AS
SELECT order_id, amount, region_id, period
FROM sales
WHERE region_id IN ({authorized_regions:Array(String)});

GRANT SELECT ON sales_secure TO mcp_sales_sa;
-- no grant on the underlying `sales` table
```

Even if the adapter is buggy or the MCP process is compromised, the service account physically cannot read the base table; the parameterized view is the only door, and the adapter fills the parameter from the PES decision, never from tool input.

**Standard:** Mechanism B for tables where a leak is a compliance incident; Mechanism A everywhere; least-privilege grants underneath both (SELECT on named views/tables only, no `SELECT ON *.*`, no `system.*`, one service account per MCP per environment).

### 7.3 Raw SQL

There is no casual `ctx.db.raw()`. If an operational escape hatch is unavoidable, it is a distinct permission (`raw_query.execute`) held only by named service identities, still wrapped by Mechanism A + view grants, and every use raises a flagged audit event.

### 7.4 Technology-agnostic adapters

The tool-author interface, the decision contract and the obligations format never change. Only the adapter (~small, per database) maps obligations onto native mechanisms:

| Database | Row predicates | Column projection | DB-level backstop |
|---|---|---|---|
| ClickHouse | `additional_table_filters` / parameterized views | SELECT list rewrite | Parameterized views + least-privilege grants |
| PostgreSQL | Native RLS + `SET LOCAL` session claims per request | SELECT list rewrite / column grants | RLS policies, column-level GRANTs |
| Oracle | VPD (Virtual Private Database) | SELECT list rewrite | VPD policies |
| REST APIs | Mandatory query parameters injected | Response-field filtering | Upstream scopes |

---

## 8. Tool Discovery vs Tool Execution

Two separate authorization checks, always:

- **Discovery (`action: "discover"`):** `list_tools` returns only tools where both the agent grant and the principal's role grant have `can_discover`. Tool descriptions and input schemas are filtered per caller — a schema field like `include_internal_costs: bool` leaks capability even when the tool is hidden.
- **Execution (`action: "execute"`):** independently checked on every invocation. A caller who manually invokes a hidden or unlisted tool is denied at execution regardless of discovery state.

Discovery filtering is UX and information hygiene; execution authorization is the security boundary.

---

## 9. Post-Execution Controls (defense in depth)

Applied by the post-hook on every response, from the same obligations object:

- Column filtering and projection re-verification (including nested JSON attributes)
- Field masking / value redaction per policy strategy
- Row-count and result-size limits; aggregation-only enforcement
- Response-schema validation against the tool's declared output schema
- Export restrictions (`export_allowed: false` blocks download/bulk-return shapes)

Two explicit boundaries of this layer:

1. Post-execution filtering is **defense in depth**. It never replaces secured query construction or database-level permissions.
2. "Prevention of sensitive-data inference" is not implementable as a deterministic control. What is shipped instead: aggregation thresholds (minimum group sizes), `max_rows` limits, and anomaly detection on extraction volume and denied-request patterns (§10). For LLM-generated summaries inside a flow, the rule is **enforcement before generation**: restricted data never enters the summarization context; post-filtering generated text is not relied upon.

---

## 10. Observability and Audit

One `trace_id` is propagated APIGEE → MCP → PES → database (OpenTelemetry). For every tool invocation the middleware emits a structured audit event:

```json
{
  "trace_id": "abc-123",
  "decision_id": "dec-9f3…",
  "timestamp": "2026-07-10T09:14:03Z",
  "principal": "u123456",
  "principal_type": "human",
  "agent_id": "agent:reporting-assistant",
  "apigee_client_id": "cli-77…",
  "mcp_id": "sales-reporting",
  "mcp_version": "1.4.2",
  "tool": "regional_sales",
  "params": { "region": "FR-IDF", "period": "2026-Q2" },   // sensitive values scrubbed
  "decision": "permit",
  "policies_evaluated": [412, 415],
  "data_source": "clickhouse:silver_sales",
  "query_fingerprint": "sha256:…",     // hash of parameterized AST, never raw SQL with values
  "rows_returned": 1284,
  "columns_returned": ["order_id","amount","region_id"],
  "filters_applied": ["row_predicate:sales","mask:customer_email"],
  "status": "success",
  "duration_ms": 142,
  "response_bytes": 88412
}
```

Rules: no secrets, tokens, private keys or confidential result payloads in logs; sensitive parameter values scrubbed by a shared scrubber; denied requests logged with the same schema (`decision: "deny"` / `"deny_unavailable"`).

The central pipeline provides: operational and security dashboards; usage reporting by team / MCP / tool / user / agent; alerting on repeated denials, unusual access patterns, excessive extraction volume, PES fail-closed events, and certificate expiry; retention per compliance requirements; and joinability of MCP audit events with PES decision logs via `decision_id` for incident investigation.

---

## 11. Credential and Secret Management

Per central standards, each MCP-owning team manages its credentials with:

- Centralized secret storage (vault); nothing in source code or images
- One service identity per MCP per environment (dev/UAT/prod separated); no shared technical account across MCPs absent strong justification
- Least-privilege database accounts (named views/tables only)
- Rotation policies and certificate-expiry monitoring (alerted centrally)
- Restricted access to private keys; documented revocation procedures
- Credential-usage auditing (vault access logs joined into the central pipeline)

Credential classes covered: APIGEE client credentials, mTLS certificates/keys, OAuth client identifiers, service-account credentials, database credentials, downstream-service secrets.

---

## 12. MCP Registry, Certification and CI/CD

**Registry.** Every MCP is declared in the PES (`mcps`, `tools`): owning team, version, data sources, tools with their `permission`/`resource`, policy references. The registry is the inventory for audits, dashboards and impact analysis.

**Certification gate.** APIGEE only routes traffic to an MCP whose registry status is `certified`. Certification checks: governance middleware integrated and non-bypassable; all tools decorated; adapter-only data access; per-MCP service identity with least-privilege grants; secrets in vault; audit events flowing; discovery/execution checks verified; fail-closed behavior verified. **No certification ⇒ no APIGEE route ⇒ no traffic.** This is the enforcement lever that makes the federated model safe.

**CI/CD compliance suite** (shipped centrally, run in every MCP repo): fails the build on undecorated tools, raw SQL outside the adapter, secrets in code, missing audit integration, schema drift against the registry, and dependency vulnerabilities. Automated re-certification runs on each release.

---

## 13. Governance Operating Model

| Concern | Central platform | MCP-owning team |
|---|---|---|
| Authentication & APIGEE standards | Define & operate | Consume |
| Identity propagation & validation | Provide library | Integrate |
| PES engine, schema, Decision API | Build & operate | — |
| Roles (global), agents, principals | Manage | Request |
| Roles (MCP-scoped), tool grants, data policies | Provide UI & schema | **Define & maintain** |
| Enforcement middleware & adapters | Build & version | Integrate & upgrade |
| MCP build, hosting, lifecycle, incidents | Standards | **Own** |
| Business use case, tools, queries | — | **Own** |
| Row/column rules for their data | Enforcement machinery | **Rule content** |
| Access reviews | Tooling & campaign schedule | Execute for their MCP |
| Audit pipeline, dashboards, alerts | Build & operate | Consume; respond to their alerts |
| Secret-management standards | Define; vault platform | Apply |
| Certification & onboarding | Define & run | Pass |

---

## 14. Target Request Flow

1. Human user or service account authenticates through APIGEE (OAuth 2.0 + PKCE, mTLS, or SA flow).
2. APIGEE validates authentication and routes to the target MCP; the request carries the verified username and the APIGEE client identity of the calling agent/application.
3. The MCP middleware verifies the request genuinely originated from APIGEE.
4. The middleware builds the `IdentityContext` (username, principal type, resolved `agent_id`, `trace_id`).
5. Discovery: `list_tools` is filtered by PES discovery decisions for this agent + principal.
6. On tool invocation, the middleware calls PES `/authorize` (or uses a valid cached decision). Deny or PES unavailable ⇒ request denied (fail closed), audited.
7. On permit, the obligations (row predicates with resolved bindings, column projection, masking, limits) are bound to the data adapter for this request.
8. The tool executes through the adapter; the secured query runs against ClickHouse via `additional_table_filters` and/or parameterized views, under a least-privilege per-MCP service account.
9. The post-hook validates and filters the response (projection, masking, limits, schema).
10. Only the authorized response is returned to the caller.
11. The audit event and OTel spans are emitted; PES decision log and MCP audit event are joinable via `decision_id`; the full path is correlated via `trace_id`.

---

## 15. Security Invariants (must always hold)

1. No MCP is reachable except through APIGEE.
2. No identity field supplied by the client is ever trusted; only APIGEE-verified username and client id.
3. An authorized agent never widens a human user's permissions: every decision intersects agent, principal and data layers.
4. Every tool execution has a PES decision (fresh or within TTL); no decision ⇒ deny.
5. All data access goes through the adapter; obligations are applied structurally, not by convention.
6. Sensitive tables are additionally protected in the database itself (views + grants), independent of application code.
7. Discovery filtering never substitutes for execution authorization.
8. Audit events exist for every permit, deny and failure, with end-to-end correlation, and contain no secrets or sensitive payloads.
9. Uncertified MCPs receive no traffic.

---

## 16. Phased Rollout

**Phase 1 — Foundations (first MCP as pilot).** PES core (schema, Decision API, minimal admin), governance middleware v1, ClickHouse adapter with Mechanism A, audit events to the central pipeline, fail-closed behavior, one pilot MCP certified end to end.

**Phase 2 — Federation.** Admin UI with team-scoped RLS, per-agent APIGEE credentials, discovery filtering, Mechanism B views for sensitive tables, CI compliance suite, certification process formalized, second and third MCPs onboarded by their teams without central hand-holding.

**Phase 3 — Scale & depth.** Access-review campaigns, anomaly detection (extraction volume, denial patterns), dashboards per team/MCP/tool/agent, decision-cache tuning, PostgreSQL adapter, reference templates and onboarding docs.

**Phase 4 — Extension.** Oracle/REST adapters, aggregation-threshold policies, approval workflows for elevated operations, cross-MCP usage analytics, periodic re-certification automation.

---

## Appendix A — Decision contract (summary)

```
POST /authorize
  in:  username, agent_id, mcp_id, tool, action(discover|execute), params_fingerprint, trace_id
  out: decision(permit|deny), roles, obligations{row_predicates[{resource,predicate,bindings}],
       column_projection[], masking[], max_rows, aggregation_only, export_allowed},
       decision_id, ttl_seconds
  failure mode: caller treats timeout/error as deny (fail closed)
```

## Appendix B — Tool-author contract

```python
@mcp.authorized_tool(permission="<domain>.<action>", resource="<logical_resource>")
async def my_tool(ctx: IdentityContext, ...business_params) -> Result:
    return await ctx.db.select(table=..., columns=[...], where={...})
```

Guarantees to the author: authentication, authorization, row/column enforcement, masking, limits, response validation and audit are handled; the tool cannot accidentally bypass them. Constraints on the author: no raw SQL, no direct database clients, no identity fields from request payloads.
