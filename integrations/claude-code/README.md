# Claude Code adapter dependencies

This is an optional host-side dependency manifest retained from the POC's
`core/package.json`. It is not a second Ensemble server and is not installed
by the Python application.

The POC declares these Node libraries for its Claude Code hooks and integration
tools. They remain available here so that porting or maintaining those adapters
does not silently replace their working libraries:

- `@xenova/transformers`
- `better-sqlite3`
- `chromadb`
- `neo4j-driver`
- `uuid`
- `js-yaml`

The POC's `pg` dependency is deliberately omitted because Ensemble's
persistence architecture is SQLite-only. The Python service backend uses the
Python `chromadb` package directly; this Node package is for host-side
integrations only.

Install this package only when developing or running the optional Node adapter
layer:

```sh
cd integrations/claude-code
npm install
```

The manifest is a dependency-preservation boundary, not a claim that every
POC hook has already been ported into Ensemble.
