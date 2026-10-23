# TTFTC test prompts (DRAFT v2: bound to the local connector files, keys not yet frozen)

15 prompts: 5 obvious, 5 vague, 5 ambiguous. All IDs below are fake.

**Status.** Tool and parameter names are now bound to `connectors/*.json`. Slack, Notion and GitHub bindings come from real schemas. Linear parameter names (other than `save_issue`) are reconstructed, and the Cloudflare tools are hand-built (see `connectors/README.md`), so keys touching those two connectors must be re-checked if they are ever replaced by real snapshots. Review the keys, then freeze them before any model run.

**Tool naming.** The harness exposes each tool as `mcp__<connector>__<tool>` (for example `mcp__github__issue_write`) because two connectors share tool names (`list_issues`). Keys below are written `connector.tool`.

## What the model sees

**System prompt (same for every trial):**

> You are an assistant with access to tools. Complete the task and return the results. Do not reply without having done the task. Do not ask for clarification; choose the most reasonable interpretation and act.

**User message (same shape for every trial):**

```
Notes:
<bullet list of IDs and facts for this prompt, including distractors>

Task: <task line>
```

The notes are the only source of identifiers. The model must copy the right ones into tool parameters.

## Scoring conventions

- Matchers: `exact`, `re:<pattern>` (case-insensitive), `any-of[...]`.
- A parameter shows green only if it satisfies its matcher; missing, wrong, or extra (not listed in the key or in the prompt's "allowed optional") shows red.
- Nested parameters are flattened to dotted rows in the grid (for example `properties.Status`).
- Every parameter listed in a key is required unless marked `(optional)`.
- Where a tool accepts the same content in two parameters (Notion comment: `markdown` or `rich_text`), the key lists both; exactly one must be present and match.
- Ambiguous prompts list one key per acceptable branch. The chosen branch is the one whose tool name the model called. Any other tool is a red tool name.
- Message/body text is matched loosely (`re:`) on purpose: the test is routing and IDs, not prose.

---

## Obvious (names the service and the action)

### O1 · Slack post

```
Notes:
- Slack channels: #releases = C07RLS4K2, #releases-archive = C05ARC9Z1, #general = C01GEN0AA2
- Current version: v2.14
- Deploy window: Thursday 14:00 UTC

Task: Post "v2.14 deploy finished" in the #releases Slack channel.
```

| Tool | `slack.slack_send_message` |
|---|---|
| `channel_id` | exact `C07RLS4K2` |
| `message` | re: `v2\.14.*deploy.*finished` |

Traps: `#releases-archive` has a near-identical name.

### O2 · GitHub comment

```
Notes:
- GitHub org: acme-labs
- Repos: billing-api, billing-web, auth-service, docs-site
- Billing API retry bug = billing-api issue 482 (open)
- Billing web issue 482 is an unrelated typo fix (closed)
- Fix PR: 517

Task: Comment "Fixed in #517" on GitHub issue 482 in the billing-api repo.
```

| Tool | `github.add_issue_comment` |
|---|---|
| `owner` | exact `acme-labs` |
| `repo` | exact `billing-api` |
| `issue_number` | exact `482` (number) |
| `body` | re: `fixed in #517` |

Traps: same issue number exists in `billing-web`.

### O3 · Cloudflare purge

```
Notes:
- Cloudflare account id: d41e8a6b0c3f4725b9e1a7c2f0d85e36
- Zones: example.com = 9f2c1d7e4b8a40c3a6e5d0f1b2c37a84, example.org = 51ab7e03c9d84f6e8a2b1c0d9e7f3a65
- Last deploy: 14:00 UTC

Task: Purge the entire Cloudflare cache for the example.com zone.
```

| Tool | `cloudflare.purge_cache` |
|---|---|
| `zone_id` | exact `9f2c1d7e4b8a40c3a6e5d0f1b2c37a84` |
| `purge_everything` | exact `true` |

Traps: the account id looks like a zone id; second zone.

### O4 · Notion comment

```
Notes:
- Notion pages: "Q4 Launch Plan" = 7c3e91a2-5b4d-4f60-9a1e-2d8b6c0f4e13, "Q3 Launch Plan" = 1a9d5f70-3e2c-48b7-b6a4-9c0e7d2f8b51
- Teamspaces: Engineering = 3f8a2c60-9d14-4b7e-a5c3-0e1f6b9d2a47
- Reviewer: Priya

Task: Add a comment "Reviewed, looks good" on the Notion page "Q4 Launch Plan".
```

| Tool | `notion.notion-create-comment` |
|---|---|
| `page_id` | exact `7c3e91a2-5b4d-4f60-9a1e-2d8b6c0f4e13` (dashes optional) |
| `markdown` **or** `rich_text[0].text.content` | re: `reviewed.*looks good` |

Traps: Q3 page id; teamspace id.

### O5 · Linear issue

```
Notes:
- Linear teams: Platform (key PLT) = a3c71e5f-2b94-4d08-9f6a-1e0d8c5b7a42, Mobile (key MOB) = c9e02b41-7d5a-4f13-8a6c-3b1f9d0e5a27
- Existing issue: PLT-77 "Pricing page"
- Sprint: 42

Task: Create a Linear issue titled "Fix login timeout" in the Platform team.
```

| Tool | `linear.save_issue` |
|---|---|
| `title` | exact `Fix login timeout` |
| `team` | any-of[`Platform`, `PLT`, `a3c71e5f-2b94-4d08-9f6a-1e0d8c5b7a42`] |
| `id` | must be absent (an `id` means update) |

Traps: Mobile team id; issue key PLT-77.

---

## Vague (one defensible answer, but never names the service or tool)

### V1 · Slack announce

```
Notes:
- Slack channels: #releases (the release crew's channel) = C07RLS4K2, #eng = C03ENGQ77, #general = C01GEN0AA2
- The deploy just finished
- On-call this week: Marcus

Task: Let the release crew know the deploy went out.
```

| Tool | `slack.slack_send_message` |
|---|---|
| `channel_id` | exact `C07RLS4K2` |
| `message` | re: `deploy|shipped|released|went out` |

### V2 · GitHub close

```
Notes:
- GitHub org: acme-labs, repo for invoices: billing-api
- Invoice rounding bug = billing-api issue 391 (open)
- Invoice PDF layout bug = billing-api issue 388 (open)
- Fix merged this morning

Task: Close out the bug about invoices rounding wrong. It's sorted.
```

| Tool | `github.issue_write` |
|---|---|
| `method` | exact `update` |
| `owner` | exact `acme-labs` |
| `repo` | exact `billing-api` |
| `issue_number` | exact `391` (number) |
| `state` | exact `closed` |
| allowed optional | `state_reason` (any-of[`completed`]) |

Traps: issue 388 is also an invoice bug.

### V3 · Cloudflare stale content

```
Notes:
- Zones: example.com = 9f2c1d7e4b8a40c3a6e5d0f1b2c37a84, example.org = 51ab7e03c9d84f6e8a2b1c0d9e7f3a65
- Cloudflare account id: d41e8a6b0c3f4725b9e1a7c2f0d85e36
- Push went live 10 minutes ago

Task: The site's serving stale pages after the push. Sort it out for the .org domain.
```

| Tool | `cloudflare.purge_cache` |
|---|---|
| `zone_id` | exact `51ab7e03c9d84f6e8a2b1c0d9e7f3a65` |
| `purge_everything` | exact `true` |

### V4 · Notion find

```
Notes:
- Notion teamspaces: Engineering = 3f8a2c60-9d14-4b7e-a5c3-0e1f6b9d2a47, Design = b2e60f94-1c7a-4d38-8e5b-7a3c9f0d1e62
- Pages in Engineering: on-call rotation, runbooks, postmortems
- The on-call doc was last edited in September

Task: Find the doc where engineering wrote down who's on call.
```

| Tool | `notion.notion-search` |
|---|---|
| `query` | re: `on-?call|rotation` |
| `teamspace_id` | exact `3f8a2c60-9d14-4b7e-a5c3-0e1f6b9d2a47` (dashes optional) |

Traps: Design teamspace id. (`filters.teamspace_ids` is not an accepted substitute; it is a different parameter, so it would show red.)

### V5 · Slack DM

```
Notes:
- Slack users: Dana Okafor (finance) = U05A9B3QX, Dana Ortiz (design) = U06B7C4RY, Marcus Lee (eng) = U04C2D8ZP
- Invoice review is tomorrow at 10:00
- Channels: #finance = C06FINNC3

Task: Ping Dana from finance about tomorrow's invoice review.
```

| Tool | `slack.slack_send_message` |
|---|---|
| `channel_id` | exact `U05A9B3QX` (the tool description says a user id works as `channel_id` for DMs) |
| `message` | re: `invoice review` |

Traps: second Dana; `#finance` is a channel, not a DM.

---

## Ambiguous (fits more than one connector; each branch has its own key)

### A1 · "ticket"

```
Notes:
- GitHub org: acme-labs, repo that owns login code: auth-service
- Linear teams: Platform (PLT) = a3c71e5f-2b94-4d08-9f6a-1e0d8c5b7a42, Mobile (MOB) = c9e02b41-7d5a-4f13-8a6c-3b1f9d0e5a27
- Login bug: users get logged out after 5 minutes
- Slack: #eng = C03ENGQ77

Task: Create a ticket for the login bug.
```

| Branch | Tool | Params |
|---|---|---|
| GitHub | `github.issue_write` | `method` exact `create`; `owner` exact `acme-labs`; `repo` exact `auth-service`; `title` re: `login` |
| Linear | `linear.save_issue` | `team` any-of[`Platform`, `PLT`, `a3c71e5f-2b94-4d08-9f6a-1e0d8c5b7a42`]; `title` re: `login`; `id` absent |

Wrong: any Slack, Cloudflare or Notion tool.

### A2 · "note on the vendor work" (reworked: Linear has no project-comment tool, so the target is an item all three connectors have)

```
Notes:
- Linear issue PLT-82 "Vendor integration"
- GitHub: acme-labs/billing-api issue 455 "Vendor integration"
- Notion page "Vendor Integration" = 0d4f7a92-8b3e-4c51-9e6a-5f1b3c7d2a80
- Slack: #payments = C09PAYQ11
- Blocker: waiting on the card vendor's API keys

Task: Leave a note on the vendor integration saying we're blocked on API keys.
```

| Branch | Tool | Params |
|---|---|---|
| Linear | `linear.save_comment` | `issueId` exact `PLT-82`; `body` re: `blocked.*api keys`; `id` absent |
| GitHub | `github.add_issue_comment` | `owner` exact `acme-labs`; `repo` exact `billing-api`; `issue_number` exact `455`; `body` re: `blocked.*api keys` |
| Notion | `notion.notion-create-comment` | `page_id` exact `0d4f7a92-8b3e-4c51-9e6a-5f1b3c7d2a80`; `markdown` or `rich_text[0].text.content` re: `blocked.*api keys` |

Wrong: Slack message (a Slack message is not a note on the work item), Cloudflare.

### A3 · "track the work"

```
Notes:
- GitHub org: acme-labs, repo: docs-site
- Linear team Platform (PLT) = a3c71e5f-2b94-4d08-9f6a-1e0d8c5b7a42
- Notion page "Docs Roadmap" = 9e7a3c15-4d82-4b60-a1f3-6c0d8e2b5f94
- Docs site currently runs on the old static generator

Task: Track the work to migrate the docs site.
```

| Branch | Tool | Params |
|---|---|---|
| GitHub | `github.issue_write` | `method` exact `create`; `owner` exact `acme-labs`; `repo` exact `docs-site`; `title` re: `migrat` |
| Linear | `linear.save_issue` | `team` any-of[`Platform`, `PLT`, `a3c71e5f-2b94-4d08-9f6a-1e0d8c5b7a42`]; `title` re: `migrat`; `id` absent |

Wrong: Notion tools (editing a roadmap page is not tracking work as an issue), Slack, Cloudflare.

### A4 · "tell the team"

```
Notes:
- Slack: #eng = C03ENGQ77, #general = C01GEN0AA2
- Checkout bug: GitHub acme-labs/billing-web issue 604, Linear PLT-91
- Fix shipped at 09:30

Task: Tell the team the checkout bug is fixed.
```

| Branch | Tool | Params |
|---|---|---|
| Slack | `slack.slack_send_message` | `channel_id` any-of[`C03ENGQ77`, `C01GEN0AA2`]; `message` re: `checkout.*fixed` |
| GitHub | `github.add_issue_comment` | `owner` exact `acme-labs`; `repo` exact `billing-web`; `issue_number` exact `604`; `body` re: `fixed` |
| Linear | `linear.save_comment` | `issueId` exact `PLT-91`; `body` re: `fixed`; `id` absent |

Wrong: Notion, Cloudflare.

### A5 · "move to done"

```
Notes:
- Notion page "Pricing Page Redesign" = e5b1c8d3-6a29-4f07-b3d4-8c0a2e9f7b16 (has a Status property: Not started / In progress / Done)
- Linear issue PLT-77 "Pricing page" (state: In Progress)
- GitHub: acme-labs/billing-web issue 612 "Pricing page copy" (open)

Task: Move the pricing page to done.
```

| Branch | Tool | Params |
|---|---|---|
| Notion | `notion.notion-update-page` | `page_id` exact `e5b1c8d3-6a29-4f07-b3d4-8c0a2e9f7b16`; `command` exact `update_properties`; `properties.Status` exact `Done` |
| Linear | `linear.save_issue` | `id` exact `PLT-77`; `state` re: `^done$` |

Wrong: GitHub close (issue 612 is copy work, not the redesign), Slack, Cloudflare.

---

## Open items before freezing

1. Replace the reconstructed Linear parameter names and the hand-built Cloudflare tools with real snapshots if a workspace/token becomes available (see `connectors/README.md`).
2. Decide the global allowed-optional parameters per tool (parameters that do not change meaning, such as pagination or formatting flags); everything else extra is red.
3. Confirm each ambiguous branch with a reviewer; drop a branch if reviewers disagree.
4. Review the keys, then freeze before any model run.
