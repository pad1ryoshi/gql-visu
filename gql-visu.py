#!/usr/bin/env python3
"""
gql_introspect.py
Parses a GraphQL introspection JSON and outputs all queries,
mutations, and subscriptions.

Usage:
    python gql_introspect.py -f schema.json
    python gql_introspect.py -f schema.json -o ./output
    python gql_introspect.py -f schema.json --depth 6
    cat schema.json | python gql_introspect.py -f -
"""

import json
import sys
import argparse
import re
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────
# SCALAR PLACEHOLDERS
# ─────────────────────────────────────────────────────────────────
SCALAR_DEFAULTS = {
    "String":    '"example"',
    "Int":       "42",
    "Float":     "3.14",
    "Boolean":   "true",
    "ID":        '"1"',
    "Date":      '"2024-01-01"',
    "DateTime":  '"2024-01-01T00:00:00Z"',
    "Time":      '"12:00:00"',
    "JSON":      "{}",
    "Upload":    "null",
    "UUID":      '"00000000-0000-0000-0000-000000000000"',
    "Long":      "1000",
    "BigInt":    "9999999",
    "BigDecimal":"9.99",
    "Url":       '"https://example.com"',
    "URI":       '"https://example.com"',
    "Email":     '"user@example.com"',
    "Any":       "null",
}

def scalar_default(name: str) -> str:
    return SCALAR_DEFAULTS.get(name, f'"<{name}>"')


# ─────────────────────────────────────────────────────────────────
# TYPE HELPERS
# ─────────────────────────────────────────────────────────────────
def unwrap(type_ref: dict) -> str:
    t = type_ref
    while t:
        if t.get("name"):
            return t["name"]
        t = t.get("ofType")
    return "Unknown"

def gql_type_str(type_ref: dict) -> str:
    kind = type_ref.get("kind")
    of   = type_ref.get("ofType")
    if kind == "NON_NULL":
        return f"{gql_type_str(of)}!"
    if kind == "LIST":
        return f"[{gql_type_str(of)}]"
    return type_ref.get("name") or "Unknown"


# ─────────────────────────────────────────────────────────────────
# SCHEMA LOADER
# ─────────────────────────────────────────────────────────────────
def load_schema(raw: dict) -> dict:
    if "data" in raw and isinstance(raw["data"], dict) and "__schema" in raw["data"]:
        return raw["data"]["__schema"]
    if "__schema" in raw:
        return raw["__schema"]
    if "types" in raw:
        return raw
    raise ValueError("Could not find '__schema' in the JSON. Make sure it's a valid introspection response.")


# ─────────────────────────────────────────────────────────────────
# SELECTION SET
# ─────────────────────────────────────────────────────────────────
def build_selection(type_name: str, types: dict, max_depth: int,
                    depth: int = 0, visited: frozenset = frozenset()) -> str:
    t = types.get(type_name)
    if not t:
        return ""

    kind = t.get("kind", "")

    if kind in ("SCALAR", "ENUM"):
        return ""

    if kind in ("UNION", "INTERFACE"):
        possible = t.get("possibleTypes") or []
        if not possible:
            return "{ __typename }"
        frags = []
        for pt in possible[:4]:
            pname = pt.get("name", "")
            if not pname or pname in visited:
                continue
            inner = build_selection(pname, types, max_depth, depth + 1, visited | {type_name})
            frags.append(f"... on {pname} {inner or '{ __typename }'}")
        return "{ __typename " + " ".join(frags) + " }"

    fields = t.get("fields") or []
    if not fields:
        return "{ __typename }"

    if depth >= max_depth or type_name in visited:
        scalars = [
            f["name"] for f in fields
            if types.get(unwrap(f["type"]), {}).get("kind") in ("SCALAR", "ENUM")
        ]
        return "{ " + " ".join(scalars[:6] or ["__typename"]) + " }"

    indent = "  " * (depth + 1)
    close  = "  " * depth
    lines  = []

    for field in fields:
        fname = field["name"]
        base  = unwrap(field["type"])
        child = types.get(base)

        if not child or child.get("kind") in ("SCALAR", "ENUM"):
            lines.append(f"{indent}{fname}")
        else:
            sub = build_selection(base, types, max_depth, depth + 1, visited | {type_name})
            lines.append(f"{indent}{fname} {sub}" if sub else f"{indent}{fname}")

    return "{\n" + "\n".join(lines) + f"\n{close}}}"


# ─────────────────────────────────────────────────────────────────
# ARG BUILDER
# ─────────────────────────────────────────────────────────────────
def serialize(val) -> str:
    if isinstance(val, str):
        if val in ("true", "false", "null") or re.match(r'^-?\d+(\.\d+)?$', val):
            return val
        if val.startswith('"'):
            return val
        return f'"{val}"'
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, dict):
        return "{" + ", ".join(f"{k}: {serialize(v)}" for k, v in val.items()) + "}"
    if isinstance(val, list):
        return "[" + ", ".join(serialize(i) for i in val) + "]"
    return str(val)

def resolve_arg_value(base: str, types: dict, depth: int = 0):
    t = types.get(base)
    if not t:
        return scalar_default(base)
    kind = t.get("kind", "")
    if kind == "SCALAR":
        return scalar_default(base)
    if kind == "ENUM":
        vals = t.get("enumValues") or []
        return vals[0]["name"] if vals else "ENUM_VALUE"
    if kind == "INPUT_OBJECT" and depth < 3:
        return {
            f["name"]: resolve_arg_value(unwrap(f["type"]), types, depth + 1)
            for f in (t.get("inputFields") or [])
        }
    return scalar_default(base)

def build_args(args: list, types: dict) -> str:
    if not args:
        return ""
    parts = [f'{a["name"]}: {serialize(resolve_arg_value(unwrap(a["type"]), types))}' for a in args]
    return "(" + ", ".join(parts) + ")"


# ─────────────────────────────────────────────────────────────────
# OPERATION GENERATOR
# ─────────────────────────────────────────────────────────────────
def generate_operations(op_type: str, root_name: Optional[str],
                        types: dict, max_depth: int) -> list[dict]:
    if not root_name or root_name not in types:
        return []

    root = types[root_name]
    fields = root.get("fields") or []
    ops = []

    for field in fields:
        name      = field.get("name", "")
        args      = field.get("args") or []
        ret_base  = unwrap(field["type"])

        arg_str   = build_args(args, types)
        selection = build_selection(ret_base, types, max_depth)
        sel_str   = f" {selection}" if selection else ""

        body = f"{op_type} {{\n  {name}{arg_str}{sel_str}\n}}"

        ops.append({
            "name":        name,
            "body":        body,
            "deprecated":  field.get("isDeprecated", False),
            "description": (field.get("description") or "").strip(),
            "args":        [a["name"] for a in args],
            "return_type": gql_type_str(field["type"]),
        })

    return ops


# ─────────────────────────────────────────────────────────────────
# SAVE
# ─────────────────────────────────────────────────────────────────
def save_results(results: dict, out_dir: str, label: str):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-_.]", "_", label)[:40]

    for category, ops in results.items():
        if not ops:
            continue
        path = f"{out_dir}/{safe}_{category}.graphql"
        with open(path, "w", encoding="utf-8") as f:
            for op in ops:
                if op["description"]:
                    f.write(f"# {op['description']}\n")
                if op["deprecated"]:
                    f.write("# [DEPRECATED]\n")
                f.write(op["body"] + "\n\n")
        print(f"  saved: {path}  ({len(ops)} operations)")


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Parse a GraphQL introspection JSON and output queries, mutations, and subscriptions."
    )
    ap.add_argument("-f", "--file", required=True, metavar="FILE",
                    help="Introspection JSON file (use '-' for stdin)")
    ap.add_argument("-o", "--output", default=None, metavar="DIR",
                    help="Save .graphql files to this directory")
    ap.add_argument("-d", "--depth", type=int, default=4, metavar="N",
                    help="Max selection set depth (default: 4)")
    ap.add_argument("--only", choices=["queries", "mutations", "subscriptions"],
                    help="Only generate one operation type")
    args = ap.parse_args()

    # Load
    try:
        if args.file == "-":
            raw = json.load(sys.stdin)
            label = "stdin"
        else:
            p = Path(args.file)
            if not p.exists():
                print(f"[!] File not found: {args.file}")
                sys.exit(1)
            raw = json.loads(p.read_text(encoding="utf-8"))
            label = p.stem
    except json.JSONDecodeError as e:
        print(f"[!] Invalid JSON: {e}")
        sys.exit(1)

    try:
        schema = load_schema(raw)
    except ValueError as e:
        print(f"[!] {e}")
        sys.exit(1)

    # Index types (skip GraphQL built-ins)
    types: dict = {}
    for t in schema.get("types") or []:
        name = t.get("name", "")
        if name and not name.startswith("__"):
            types[name] = t

    qt = schema.get("queryType")        or {}
    mt = schema.get("mutationType")     or {}
    st = schema.get("subscriptionType") or {}

    query_root        = qt.get("name") if isinstance(qt, dict) else None
    mutation_root     = mt.get("name") if isinstance(mt, dict) else None
    subscription_root = st.get("name") if isinstance(st, dict) else None

    # Generate
    results = {
        "queries":       generate_operations("query",        query_root,        types, args.depth),
        "mutations":     generate_operations("mutation",     mutation_root,     types, args.depth),
        "subscriptions": generate_operations("subscription", subscription_root, types, args.depth),
    }

    if args.only:
        results = {k: v for k, v in results.items() if k == args.only}

    # Print
    total = sum(len(v) for v in results.values())

    if total == 0:
        print("[!] No operations found in this schema.")
        sys.exit(0)

    for category, ops in results.items():
        if not ops:
            continue
        print(f"\n# {'─'*50}")
        print(f"# {category.upper()}  ({len(ops)})")
        print(f"# {'─'*50}\n")
        for op in ops:
            if op["description"]:
                print(f"# {op['description']}")
            if op["deprecated"]:
                print("# [DEPRECATED]")
            print(op["body"])
            print()

    print(f"# Total: {total} operations")

    # Save
    if args.output:
        save_results(results, args.output, label)


if __name__ == "__main__":
    main()
