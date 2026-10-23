"""Connector variants for leverage.md: nine dials, each with levels L0 (worse than shipped) .. L3 (best); L1 = as shipped.

Only the GitHub and Linear tools are varied. Every variant tool keeps a pointer to the canonical tool it stands for plus
`to` / `frm` mappings, so the unchanged answer keys in cases.json still score every call (variant call -> canonical call),
and tests can prove each key is solvable in every variant (canonical call -> variant call).
"""
import copy
import hashlib
import json
import re
from pathlib import Path

from . import connectors, episode

ROOT = Path(__file__).resolve().parent.parent
DIALS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9"]
SUBJECT = ("github", "linear")
HOLDS = {"D5": 1, "D7": 1}  # these dials have no level worse than shipped: L0 behaves as L1

CORE = {  # D1: tools kept at L2 (10 per connector) and L3 (the first 5)
    "github": ["issue_read", "issue_write", "add_issue_comment", "list_issues", "search_issues",
               "get_me", "get_file_contents", "search_repositories", "pull_request_read", "list_pull_requests"],
    "linear": ["save_issue", "save_comment", "get_issue", "list_issues", "list_teams",
               "list_comments", "list_issue_statuses", "list_projects", "list_users", "get_team"],
}
EXTRA_PARAMS = {  # D6 L0: plausible optional parameters that invite extra arguments
    "notify_subscribers": {"type": "boolean", "description": "Whether to notify subscribers about this change."},
    "dry_run": {"type": "boolean", "description": "Validate the request without applying it."},
    "client_request_id": {"type": "string", "description": "Optional idempotency key supplied by the caller."},
    "source": {"type": "string", "description": "Free-form label identifying the calling application."},
    "visibility": {"type": "string", "enum": ["default", "internal", "public"], "description": "Visibility of the resulting record."},
}
COMMON = {"body", "title", "labels", "state", "description", "priority"}
ID_RENAME = {"github": {"owner": "account", "repo": "name", "issue_number": "id"}, "linear": {"id": "ref", "issueId": "issue"}}
READABLE = {"issue_read": "read_issue", "issue_write": "write_issue", "add_issue_comment": "comment_on_issue",
            "pull_request_read": "read_pull_request", "list_pull_requests": "list_pull_requests"}
BLOAT = ("This tool provides comprehensive, enterprise-grade functionality for working with {noun} through the underlying {service} "
         "platform APIs. It can be utilized in a wide variety of workflows including triage, auditing, reporting, automation and "
         "integration scenarios, and supports a broad range of optional parameters to give you maximum flexibility. When invoked it "
         "performs the requested operation on your behalf and returns a rich, fully structured payload describing the outcome, "
         "including identifiers, timestamps, related entities and metadata that may be useful for downstream processing. Use this "
         "tool whenever you need to interact with {service} {noun}; consult the parameter descriptions for details about each "
         "field. Note that availability of some fields may depend on workspace configuration, permissions and plan. Results are "
         "returned in a consistent format that is designed to be forward compatible, so additional properties may be added over time and "
         "callers should be prepared to ignore properties they do not recognise. Rate limits and organisation policies apply.")
# D2 L2 / L3 wording by role (None keeps the shipped text). Text never names parameters or tools, so renames cannot break it.
DESC = {
    "github.issue_read": ("Read one issue: its details, comments, sub-issues or labels, chosen with `method`.",
                          "Read one existing issue (details, comments, sub-issues or labels, chosen with `method`). Use it only to find out "
                          "what an issue says. Do not call it before commenting on, editing or closing an issue when the request already "
                          "gives the repository and issue number: call the write tool directly."),
    "github.issue_write": ("Create a new issue, or update an existing one (title, body, labels, assignees, state; close it with state=closed), "
                           "chosen with `method`.",
                           "Create or update an issue. method=create makes a new issue (needs a title). method=update edits an existing issue "
                           "by number; set state=closed (state_reason completed or not_planned) to close it. No prior read is needed when "
                           "the repository and issue number are known."),
    "github.issue_write#create": ("Create a new issue in a repository. Needs a title.",
                                  "Create a new issue in a repository. Needs a title. No prior lookup is needed."),
    "github.issue_write#update": ("Update an existing issue by number: title, body, labels, assignees or state.",
                                  "Update an existing issue by number (title, body, labels, assignees, state). No prior read is needed when "
                                  "the repository and issue number are known."),
    "github.issue_write#close": ("Close an issue by number.",
                                 "Close an issue by number (reason: completed or not_planned). No prior read is needed when the repository "
                                 "and issue number are known."),
    "github.add_issue_comment": ("Add a comment to an issue or pull request by number.",
                                 "Add a comment to an existing issue or pull request by number. No prior read is needed when the repository "
                                 "and number are known."),
    "github.list_issues": ("List a repository's issues, filtered by state, labels or date. For browsing; to find an issue by topic use search.",
                           "List a repository's issues (browse only). To find an issue by topic use search; do not list just to look up an "
                           "issue whose number is already known."),
    "github.search_issues": ("Search issues across GitHub by keywords and qualifiers.",
                             "Search issues by keywords and qualifiers to find an issue whose number is unknown. Not needed when the issue "
                             "number is given."),
    "linear.save_issue": (None, "{shipped} No prior lookup is needed: team names, keys and ids from the request can be used directly."),
    "linear.get_issue": (None, "{shipped} Only needed to read an issue; not required before updating or commenting when the identifier is known."),
    "linear.list_issues": (None, "{shipped} For browsing; not needed to update or comment on an issue whose identifier is known."),
    "linear.save_comment": (None, "{shipped} No prior lookup is needed when the issue identifier is known."),
}
D9_SAME = {"write": "Create or update an issue.", "create": "Create an issue.", "update": "Update an issue.",
           "comment": "Add a comment to an issue.", "list": "List issues.", "read": "Get issue details."}
D9_GROUP = {"github.issue_write": "write", "linear.save_issue": "write", "github.issue_write#create": "create", "linear.save_issue#create": "create",
            "github.issue_write#update": "update", "linear.save_issue#update": "update", "github.add_issue_comment": "comment",
            "linear.save_comment": "comment", "github.list_issues": "list", "linear.list_issues": "list",
            "github.issue_read": "read", "linear.get_issue": "read"}
D9_SCOPE = {"github": ("GitHub (code repositories: bugs and pull requests): ", " For tracking planned work use Linear instead."),
            "linear": ("Linear (planned team work: tickets and projects): ", " For bugs in a code repository use GitHub instead.")}
INSTRUCTIONS = {
    2: "Server instructions (github, linear): repository names, issue numbers and team ids are given in the user's notes; use them directly.",
    3: "Server instructions (github, linear): repository names, issue numbers and team ids are given in the user's notes; use them directly. "
       "GitHub issues track bugs and work in a code repository; Linear issues track planned team work. If the request names neither, "
       "either is acceptable: pick one and act. Do not look up an item before changing it when its identifier is already in the notes.",
}


def _cases():
    return json.loads((ROOT / "cases.json").read_text())["cases"]


def key_tools():
    """Canonical tools any answer key uses, and for each the parameter names its keys (or allowed extras) mention."""
    protect = {}
    for case in _cases():
        for branch in case["accept"]:
            names = protect.setdefault(branch["tool"], set())
            for key in list(branch["params"]) + list(branch.get("allowed_optional") or {}):
                names.update(alt.split(".")[0] for alt in key.split("|"))
    return protect


def _load(path):
    data = json.loads(Path(path).read_text())
    return data["connector"], data["tools"]


def _tool(connector, raw):
    return {"connector": connector, "logical": f"{connector}.{raw['name']}", "role": f"{connector}.{raw['name']}", "name": raw["name"],
            "description": raw["description"], "schema": copy.deepcopy(raw["input_schema"]),
            "to": lambda i: i, "frm": lambda c: c, "accepts": lambda c: True}


def _step(t, to=None, frm=None, **changes):
    """Copy of t with `changes`; the new variant->previous-variant mapping is composed in front of the old one."""
    n = {**t, **changes}
    if to:
        n["to"] = lambda i, o=t["to"], f=to: o(f(i))
    if frm:
        n["frm"] = lambda c, o=t["frm"], f=frm: f(o(c))
    return n


def _schema(props, required):
    return {"type": "object", "properties": props, "required": [r for r in required if r in props]}


def _narrow(t, name, keep, required, description, accepts, role=None, to=None, frm=None):
    props = {k: v for k, v in t["schema"]["properties"].items() if k in keep}
    return _step(t, to=to, frm=frm, name=name, description=description, schema=_schema(props, required), accepts=accepts, role=role or t["role"])


# ---------- dials ----------

def d1(level, shipped, full_github):
    tools = {c: [_tool(c, r) for r in raws] for c, raws in shipped.items()}
    if level == 0:
        tools["github"] = [_tool("github", r) for r in full_github]
    elif level >= 2:
        for c in SUBJECT:
            keep = CORE[c][:10 if level == 2 else 5]
            tools[c] = [t for t in tools[c] if t["name"] in keep]
    return tools


def d5(tools, level):
    if level < 2:
        return tools
    out = []
    for t in tools:
        lg, props = t["logical"], t["schema"]["properties"]
        if lg == "github.issue_write":
            common = ["owner", "repo", "title", "body", "labels", "assignees", "milestone", "type"]
            out.append(_narrow(t, "create_issue", common, ["owner", "repo", "title"], "Create a new issue in a repository.",
                               lambda c: c.get("method") == "create", role=lg + "#create",
                               to=lambda i: {**i, "method": "create"}, frm=lambda c: {k: v for k, v in c.items() if k != "method"}))
            out.append(_narrow(t, "update_issue", common + ["issue_number", "state", "state_reason", "duplicate_of"], ["owner", "repo", "issue_number"],
                               "Update an existing issue in a repository.",
                               (lambda c: c.get("method") == "update" and c.get("state") != "closed") if level == 3 else (lambda c: c.get("method") == "update"),
                               role=lg + "#update", to=lambda i: {**i, "method": "update"}, frm=lambda c: {k: v for k, v in c.items() if k != "method"}))
            if level == 3:
                out.append(_narrow(t, "close_issue", ["owner", "repo", "issue_number", "state_reason"], ["owner", "repo", "issue_number"], "Close an issue.",
                                   lambda c: c.get("method") == "update" and c.get("state") == "closed", role=lg + "#close",
                                   to=lambda i: {**i, "method": "update", "state": "closed"},
                                   frm=lambda c: {k: v for k, v in c.items() if k not in ("method", "state")}))
        elif lg == "linear.save_issue":
            out.append(_narrow(t, "create_issue", [k for k in props if k != "id"], ["title", "team"], "Create a Linear issue.",
                               lambda c: "id" not in c, role=lg + "#create"))
            out.append(_narrow(t, "update_issue", list(props), ["id"], "Update an existing Linear issue by `id`.", lambda c: "id" in c, role=lg + "#update"))
        elif level == 3 and lg == "github.add_issue_comment":
            out.append(_narrow(t, "comment_on_issue", ["owner", "repo", "issue_number", "body"], ["owner", "repo", "issue_number", "body"],
                               "Add a comment to an issue or pull request.", lambda c: "comment_id" not in c and "reaction" not in c))
        elif level == 3 and lg == "linear.save_comment":
            out.append(_narrow(t, "comment_on_issue", ["issueId", "body", "parentId"], ["issueId", "body"], "Create a comment on a Linear issue.",
                               lambda c: "id" not in c))
        else:
            out.append(t)
    return out


def d6(tools, level):
    if level in (1,):
        return tools
    protect, out = key_tools(), []
    for t in tools:
        props, req = t["schema"]["properties"], t["schema"].get("required", [])
        if level == 0:
            if not episode.is_read_only(t["logical"]):
                props = {**props, **{k: v for k, v in EXTRA_PARAMS.items() if k not in props}}
        else:
            keep = set(req) | protect.get(t["logical"], set()) | (COMMON if level == 2 else set())
            props = {k: v for k, v in props.items() if k in keep}
        out.append(_step(t, schema=_schema(props, req)))
    return out


def _split_repo(value, tolerant):
    if not isinstance(value, str):
        return None
    x = value.strip() if tolerant else value
    if tolerant:
        x = re.sub(r"^https?://github\.com/", "", x).removesuffix(".git").strip("/")
    parts = x.split("/")
    return (parts[0], parts[1]) if len(parts) == 2 and all(parts) else None


def _merge_repo(t, tolerant):
    props = t["schema"]["properties"]
    if "owner" not in props or "repo" not in props:
        return t
    new = {k: v for k, v in props.items() if k not in ("owner", "repo")}
    new["repo"] = {"type": "string", "description": "Repository as 'owner/name', e.g. 'acme-labs/billing-api'."}
    if "issue_number" in new:
        new["issue_number"] = {"type": ["integer", "string"], "description": "Issue number, e.g. 482 or '#482'."}
    req = t["schema"].get("required", [])
    req = [r for r in req if r not in ("owner", "repo")] + (["repo"] if "owner" in req or "repo" in req else [])

    def to(i):
        i = dict(i)
        pair = _split_repo(i.get("repo"), tolerant)
        if pair:
            i["owner"], i["repo"] = pair
        if isinstance(i.get("issue_number"), str):
            s = i["issue_number"].strip().lstrip("#")
            i["issue_number"] = int(s) if s.isdigit() else i["issue_number"]
        return i

    def frm(c):
        c = dict(c)
        if "owner" in c and "repo" in c:
            c["repo"] = f"{c.pop('owner')}/{c['repo']}"
        return c

    return _step(t, to=to, frm=frm, schema=_schema(new, req))


def _rename(t, mapping):
    props = t["schema"]["properties"]
    m = {k: v for k, v in mapping.items() if k in props and v not in props}
    if not m:
        return t
    inv = {v: k for k, v in m.items()}
    new = {m.get(k, k): ({**v, "description": "The identifier."} if k in m else v) for k, v in props.items()}
    return _step(t, to=lambda i: {inv.get(k, k): v for k, v in i.items()}, frm=lambda c: {m.get(k, k): v for k, v in c.items()},
                 schema=_schema(new, [m.get(r, r) for r in t["schema"].get("required", [])]))


def d3(tools, level):
    if level == 1:
        return tools
    if level == 0:
        return [_rename(t, ID_RENAME[t["connector"]]) for t in tools]
    return [_merge_repo(t, tolerant=level == 3) if t["connector"] == "github" else t for t in tools]


def d4(tools, level):
    if level == 1:
        return tools
    out = []
    for t in tools:
        n = t["name"]
        if level == 0:
            n = n.replace("issue", "item").replace("pull_request", "change").replace("comment", "note")
        else:
            n = READABLE.get(n, n) if level == 3 else n
            n = n if n.startswith(t["connector"] + "_") else f"{t['connector']}_{n}"
        out.append({**t, "name": n})
    return out


def d2(tools, level):
    if level == 1:
        return tools
    out = []
    for t in tools:
        text = t["description"]
        if level == 0:
            noun = re.sub(r"[_-]+", " ", t["name"])
            text = f"{text} {BLOAT.format(noun=noun, service=t['connector'].capitalize())}"
        elif t["role"] in DESC and DESC[t["role"]][level - 2]:
            text = DESC[t["role"]][level - 2].format(shipped=t["description"])
        out.append({**t, "description": text})
    return out


def d9(tools, level):
    if level == 1:
        return tools
    out = []
    for t in tools:
        text, pre, post = t["description"], *D9_SCOPE[t["connector"]]
        if level == 0 and t["role"] in D9_GROUP:
            text = D9_SAME[D9_GROUP[t["role"]]]
        elif level >= 2 and (t["role"] in D9_GROUP or t["role"] == "github.search_issues"):
            text = pre + text + (post if level == 3 else "")
        out.append({**t, "description": text})
    return out


# ---------- D8: mock results and errors ----------

def _user(k):
    base = f"https://api.github.com/users/user{k}"
    return {"login": f"user{k}", "id": 1000 + k, "node_id": f"MDQ6VXNlcj{k}", "avatar_url": f"https://avatars.githubusercontent.com/u/{1000 + k}?v=4",
            "gravatar_id": "", "url": base, "html_url": f"https://github.com/user{k}", "followers_url": base + "/followers",
            "following_url": base + "/following{/other_user}", "gists_url": base + "/gists{/gist_id}", "starred_url": base + "/starred{/owner}{/repo}",
            "subscriptions_url": base + "/subscriptions", "organizations_url": base + "/orgs", "repos_url": base + "/repos",
            "events_url": base + "/events{/privacy}", "received_events_url": base + "/received_events", "type": "User", "site_admin": False}


def verbose_issue(logical, i):
    """A realistic, bulky issue object (the shape real GitHub / Linear responses have)."""
    if logical.startswith("linear."):
        ref = str(i.get("id", "PLT-1"))
        return {"id": "0c1d2e3f-4a5b-4c6d-8e7f-90a1b2c3d4e5", "identifier": ref, "title": "", "description": "", "priority": 3, "estimate": None,
                "state": {"id": "s-1", "name": "Todo", "type": "unstarted", "color": "#e2e2e2", "position": 0},
                "team": {"id": "t-1", "key": "PLT", "name": "Platform", "private": False, "timezone": "UTC"},
                "project": None, "cycle": None, "labels": {"nodes": []}, "assignee": None, "creator": {"id": "u-1", "name": "user1", "email": "user1@acme.dev"},
                "attachments": {"nodes": []}, "history": {"nodes": [{"id": f"h-{k}", "createdAt": "2026-10-01T09:00:00Z", "actor": {"id": "u-1"}, "fromStateId": None,
                                                                       "toStateId": "s-1", "updatedDescription": False, "archived": False} for k in range(8)]},
                "url": f"https://linear.app/acme/issue/{ref}", "branchName": ref.lower() + "-issue", "createdAt": "2026-10-01T09:00:00Z",
                "updatedAt": "2026-10-02T09:00:00Z", "subscribers": {"nodes": [{"id": f"u-{k}", "name": f"user{k}"} for k in range(4)]}}
    n, o, r = i.get("issue_number", 1), i.get("owner", "org"), i.get("repo", "repo")
    api = f"https://api.github.com/repos/{o}/{r}"
    label = lambda k: {"id": 200 + k, "node_id": f"LA_{k}", "url": f"{api}/labels/l{k}", "name": f"label-{k}", "color": "d73a4a", "default": False, "description": ""}
    return {"url": f"{api}/issues/{n}", "repository_url": api, "labels_url": api + f"/issues/{n}/labels{{/name}}", "comments_url": api + f"/issues/{n}/comments",
            "events_url": api + f"/issues/{n}/events", "html_url": f"https://github.com/{o}/{r}/issues/{n}", "id": 4000000 + n, "node_id": f"I_kwDO{n}",
            "number": n, "title": "", "user": _user(1), "labels": [label(k) for k in range(3)], "state": "open", "locked": False, "assignee": _user(2),
            "assignees": [_user(2), _user(3)], "milestone": {"url": api + "/milestones/1", "id": 7, "number": 1, "title": "next", "creator": _user(4),
                                                             "open_issues": 3, "closed_issues": 9, "state": "open"},
            "comments": 0, "created_at": "2026-10-01T09:00:00Z", "updated_at": "2026-10-02T09:00:00Z", "closed_at": None,
            "author_association": "MEMBER", "active_lock_reason": None, "body": "", "reactions": {"url": api + f"/issues/{n}/reactions", "total_count": 0,
                                                                                                 "+1": 0, "-1": 0, "laugh": 0, "hooray": 0, "confused": 0, "heart": 0, "rocket": 0, "eyes": 0},
            "timeline_url": api + f"/issues/{n}/timeline", "performed_via_github_app": None, "state_reason": None}


def make_mock(level, by_name):
    def mock(name, raw, logical, cinput):
        t = by_name.get(name)
        missing = [r for r in t["schema"].get("required", []) if r not in raw] if t and level != 1 else []
        if missing:
            if level == 0:
                return json.dumps({"error": "E_VALIDATION"})
            hint = t["schema"]["properties"][missing[0]].get("description", "") if level == 3 else ""
            return json.dumps({"error": f"Missing required parameter '{missing[0]}'. {hint}".strip()})
        if logical in ("github.issue_read", "linear.get_issue") and level != 1:
            if level == 0:
                return json.dumps(verbose_issue(logical, cinput))
            out = {"number": cinput.get("issue_number"), "title": "", "state": "open"} if logical.startswith("github") else \
                {"id": cinput.get("id"), "title": "", "state": "Todo"}
            if level == 3:
                out["next_step"] = "You already have what you need: if the request was to change or comment on this item, call the write tool now."
            return json.dumps(out)
        return episode.mock_result(logical, cinput)
    return mock


# ---------- build ----------

class Variant:
    def __init__(self, levels, tools, system, by_name, working, mock):
        self.levels, self.tools, self.system, self.mock = levels, tools, system, mock
        self._by_name, self._working = by_name, working
        self.hash = hashlib.sha1(json.dumps({"tools": tools, "system": system, "d8": levels.get("D8", 1)}, sort_keys=True).encode()).hexdigest()[:12]

    def canonical(self, name, tool_input):
        t = self._by_name.get(name)
        return (t["logical"], t["to"](tool_input)) if t else (connectors.logical(name), tool_input)

    def express(self, logical, cinput):
        """The (variant tool name, variant input) that says canonical call (logical, cinput): used by tests and oracle checks."""
        for t in self._working:
            if t["logical"] == logical and t["accepts"](cinput):
                return f"mcp__{t['connector']}__{t['name']}", t["frm"](cinput)
        return f"mcp__{logical.split('.')[0]}__{logical.split('.')[1]}", cinput


def build(levels, system):
    lv = {d: levels.get(d, 1) for d in DIALS}
    lv.update({d: 1 for d, v in HOLDS.items() if lv[d] < v})
    shipped = {}
    for path in sorted(connectors.DIR.glob("*.json")):
        c, raws = _load(path)
        shipped[c] = raws
    _, full = _load(ROOT / "variants" / "github_full.json")
    tools = d1(lv["D1"], shipped, full)
    sub = [t for c in SUBJECT for t in tools[c]]
    if levels.get("minimal"):
        keep = set(key_tools())
        sub = [t for t in sub if t["logical"] in keep]
    for fn, d in ((d5, "D5"), (d6, "D6"), (d3, "D3"), (d4, "D4")):
        sub = fn(sub, lv[d])
    sub = d9(sub, lv["D9"] if lv["D9"] == 0 else 1)  # identical wording is the base text, so D2's bloat still goes on top
    sub = d2(sub, lv["D2"])
    sub = d9(sub, lv["D9"] if lv["D9"] >= 2 else 1)  # scoping is added in front of whatever D2 produced
    if levels.get("minimal"):
        sub = [{**t, "description": re.sub(r"[_-]+", " ", t["name"]).capitalize() + "."} for t in sub]
    names = [(t["connector"], t["name"]) for t in sub]
    assert len(names) == len(set(names)) and all(len(f"mcp__{c}__{n}") <= 64 for c, n in names), "tool names must be unique and <= 64 chars"
    api, by_name, working = [], {}, []
    for c in sorted(shipped):
        mine = [t for t in sub if t["connector"] == c] if c in SUBJECT else [_tool(c, r) for r in shipped[c]]
        for t in mine:
            name = f"mcp__{c}__{t['name']}"
            api.append({"name": name, "description": t["description"], "input_schema": t["schema"]})
            by_name[name] = t
            working.append(t)
    suffix = INSTRUCTIONS.get(lv["D7"])
    return Variant(lv | ({"minimal": True} if levels.get("minimal") else {}), api, system + ("\n\n" + suffix if suffix else ""), by_name, working,
                   make_mock(lv["D8"], by_name))
