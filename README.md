# gql-visu
A vibe-coded tool that analyzes an introspection of a GraphQL endpoint and assembles the queries and mutations.

This tool was built out of a simple frustration: existing tools like GraphQL Voyager and some Burp/Caido plugins are too strict about the introspection format. A missing directive field or a slightly non-standard schema envelope is enough to throw errors like `Introspection result missing directive locations` and leave you with nothing to work with.

# use

```bash
# Print everything to stdout
python gql_introspect.py -f schema.json

# Save .graphql files per category
python gql_introspect.py -f schema.json -o ./output

# Filter one type
python gql_introspect.py -f schema.json --only mutations

# Deeper selection sets (useful for complex schemas)
python gql_introspect.py -f schema.json --depth 6

# Pipe from stdin (e.g. straight from curl/Caido)
cat response.json | python gql_introspect.py -f -
```

# example of use

```python
python3.12.exe .\gql-visu.py -f .\introspection.json

# ──────────────────────────────────────────────────
# QUERIES  (1)
# ──────────────────────────────────────────────────

query {
  getUser(id: 42) {
  id
  username
}
}


# ──────────────────────────────────────────────────
# MUTATIONS  (1)
# ──────────────────────────────────────────────────

mutation {
  deleteOrganizationUser(input: {id: 42}) {
  user {
    id
    username
  }
}
}

# Total: 2 operations
```
