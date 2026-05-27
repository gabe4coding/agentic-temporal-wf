# Pattern-C Platform Boundary

## Platform Contract

PR autofix is an adapter over five platform concepts:

| Concept | Responsibility |
| --- | --- |
| `RunContext` | Durable workload and workspace identity. |
| `SandboxHandle` | Container identifier plus the fixed `/work` mapping; it contains no credential. |
| `CapabilityBinding` | Broker-only mapping from an opaque run token to workflow, subject, capabilities, and expiration. |
| `OperationResult` | Durable record of approval and external publication result. |
| Temporal workflow | Durable control plane for events, approval, retries, and status. |

The implemented adapter adds `PRRef`, GitHub webhook projection, PR context
tools, and PR status rendering. A second workload can reuse the run,
sandbox, broker registration, operation, and audit contracts without granting
new sandbox authority.

## Execution Flow

1. The gateway verifies a webhook and starts or signals `PRAutofixWorkflow`.
2. The trusted worker clones or fetches into `${WORKSPACE_ROOT}/autofix-<workflow>/repo`.
   Network Git operations carry authorization transiently; `.git/config`
   retains a credential-free origin.
3. The provisioner registers an opaque run token with the capability broker
   and starts a container with only that repository mounted as `/work`.
4. Claude Agent SDK uses the broker relay for model calls and the broker
   remote HTTP MCP for PR reads and publication requests.
5. File edits and local `ruff`/`pytest` execution remain inside `/work`.
6. `request_push_changes` becomes a Temporal Workflow Update. An authorized
   `/autofix approve <approval_id>` issue comment releases the operation.
7. A trusted activity pushes to `PRRef.head_ref`, stamping
   `Autofix-Idempotency: <operation_key>`. A retry discovers an already
   published keyed commit instead of creating a second commit.

## Capabilities

Broker bindings authorize generic capability classes:

| Capability | PR adapter operation |
| --- | --- |
| `model.invoke` | Relay approved Anthropic message API calls. |
| `source.read` | `get_pr_context`, `list_review_comments`, `list_check_results`. |
| `changes.publish` | `request_push_changes`; the broker cannot directly push. |

The MCP broker resolves repository and PR number from `CapabilityBinding`.
Tool arguments cannot choose another subject. Status comment posting is
workflow-owned and is not exposed to the sandbox.

## Credentials And Isolation

The untrusted container has no Docker socket, GitHub credential, or Anthropic
credential. Its upstream-facing values are:

```text
RUN_CAPABILITY_TOKEN=<opaque run token>
ANTHROPIC_BASE_URL=http://capability-broker:8443/anthropic
CAPABILITY_MCP_URL=http://capability-broker:8443/mcp
```

The Claude subprocess places the opaque token in its SDK API-key header slot;
the relay validates that token and substitutes the broker-held Anthropic key
upstream. The GitHub PAT is a PoC trusted-service credential only; use a
GitHub App for production deployments.

## Approval And Audit

Publication is the only human-gated agent action. `APPROVER_LOGINS` is
fail-closed: an unset list authorizes nobody. The operation key is derived
from `workflow_id`, iteration, and `push_changes`; its decision and pushed
commit SHA are persisted in workflow state as an `OperationResult`.
