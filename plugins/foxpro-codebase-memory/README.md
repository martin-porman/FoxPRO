# FoxPro Codebase Memory

Local, dependency-free structural indexing for Visual FoxPro recovery work. It reads source files and VFP source containers without running Visual FoxPro, and writes SQLite indexes only under `~/.cache/foxpro-codebase-memory/indexes` unless `FOXPRO_MEMORY_CACHE` is set.

The plugin is a companion to `codebase-memory-mcp`. It does not replace or modify the installed generic indexer.

## Indexed inputs

- `.prg`, `.mpr`, and `.h` source files
- Form, class-library, menu, report, and database-definition containers: `.scx/.sct`, `.vcx/.vct`, `.mnx/.mnt`, `.frx/.frt`, and `.dbc/.dct`
- Other files as explicit opaque coverage records

Form and library source retains original path, hash, record number, memo block/range, object identity, class metadata, and decoding warnings. Customer DBF rows are never decoded as application source.

## Using it

For Codex, add the GitHub marketplace and install the plugin:

```sh
codex plugin marketplace add martin-porman/FoxPRO
codex plugin add foxpro-codebase-memory@foxpro
```

Restart Codex or start a new task after installation. The plugin runs entirely locally with Python 3.10 or newer and no third-party Python packages.

Use the MCP tools:

1. `index_repository` with a source root and a lowercase project name.
2. `index_status` and `check_index_coverage` before relying on results.
3. `search_graph`, `get_code_snippet`, and `trace_path` for evidence-backed exploration.

The equivalent command line interface is:

```sh
python3 scripts/foxpro-memory.py build /path/to/source --project my-project
python3 scripts/foxpro-memory.py search --project my-project --query methodname
```

Indexes are stored below `~/.cache/foxpro-codebase-memory/indexes/` by default. Use one project name per recovered source version. Keep a deployed artifact capture separate from an independently recovered source tree so a graph never silently combines different revisions.

## Limits

This is a conservative lexical structural parser. It resolves only proven lexical, file, or explicit-library relationships. Macro substitution, `EVALUATE`, unknown receivers, runtime code and ambiguous targets remain unresolved. A coverage result with no recorded issue is not a proof of runtime behavior or complete static resolution.

The main Windows binaries remain opaque when their container structure cannot be recovered. Their files and hashes are retained as coverage evidence rather than being claimed as indexed code.
