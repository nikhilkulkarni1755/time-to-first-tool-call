"""Parameter-level scoring: every parameter is its own green/red row."""
import re

MISSING = object()


def get_path(obj, path):
    """Value at a dotted path (list indexes are numbers), or MISSING."""
    for part in path.split("."):
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return MISSING
    return obj


def leaves(obj, prefix=""):
    """(path, value) for every scalar (or empty container) in a nested input."""
    if isinstance(obj, dict) and obj:
        for k, v in obj.items():
            yield from leaves(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list) and obj:
        for i, v in enumerate(obj):
            yield from leaves(v, f"{prefix}.{i}" if prefix else str(i))
    else:
        yield prefix, obj


def _same(expected, actual):
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return expected == actual
    return type(expected) is type(actual) and expected == actual


def matches(matcher, actual):
    """True if the actual value satisfies the matcher."""
    if isinstance(matcher, dict):
        if "absent" in matcher:
            return actual is MISSING
        if actual is MISSING:
            return False
        if "re" in matcher:
            return isinstance(actual, str) and re.search(matcher["re"], actual, re.I | re.S) is not None
        if "uuid" in matcher:
            return isinstance(actual, str) and actual.replace("-", "").lower() == matcher["uuid"].replace("-", "").lower()
        if "any" in matcher:
            return any(matches(m, actual) for m in matcher["any"])
    return actual is not MISSING and _same(matcher, actual)


def describe(matcher):
    if isinstance(matcher, dict):
        if "absent" in matcher:
            return "(must be absent)"
        if "re" in matcher:
            return f"~ /{matcher['re']}/"
        if "uuid" in matcher:
            return matcher["uuid"]
        if "any" in matcher:
            return " | ".join(describe(m) for m in matcher["any"])
    return repr(matcher)


def _reason(matcher, actual):
    if isinstance(matcher, dict) and "absent" in matcher:
        return "sent but must be absent"
    return "missing" if actual is MISSING else "wrong value"


def _branch_for(accept, called):
    for branch in accept:
        if branch["tool"] == called:
            return branch, True
    same_connector = [b for b in accept if b["tool"].split(".")[0] == called.split(".")[0]]
    return (same_connector or accept)[0], False


def score(case, called, tool_input):
    """Grid for one tool call: tool row, one row per key parameter, one row per unexpected parameter."""
    branch, tool_ok = _branch_for(case["accept"], called)
    rows, covered = [], []
    for key, matcher in branch["params"].items():
        alts = key.split("|")
        present = [a for a in alts if get_path(tool_input, a) is not MISSING]
        if len(alts) > 1 and len(present) != 1:
            reason = "missing" if not present else "both alternatives sent"
            rows.append({"path": key, "expected": describe(matcher), "actual": None, "ok": False, "reason": reason})
            covered += present
            continue
        path = present[0] if present else alts[0]
        actual = get_path(tool_input, path)
        covered.append(path)
        ok = matches(matcher, actual)
        rows.append({"path": path, "expected": describe(matcher), "actual": None if actual is MISSING else actual,
                     "ok": ok, "reason": None if ok else _reason(matcher, actual)})
    allowed = branch.get("allowed_optional") or {}
    for path, value in leaves(tool_input):
        if any(path == c or path.startswith(c + ".") for c in covered):
            continue
        if path in allowed:
            matcher = allowed[path]
            ok = matcher is None or matches(matcher, value)
            rows.append({"path": path, "expected": "(optional) " + (describe(matcher) if matcher else "any"),
                         "actual": value, "ok": ok, "reason": None if ok else "wrong value"})
        else:
            rows.append({"path": path, "expected": "(not allowed)", "actual": value, "ok": False, "reason": "unexpected parameter"})
    return {
        "tool": {"expected": [b["tool"] for b in case["accept"]], "actual": called, "ok": tool_ok},
        "params": rows,
        "pass": tool_ok and all(r["ok"] for r in rows),
    }
