# FoxPro Codebase Memory

Local, dependency-free evidence-graph indexing for Visual FoxPro recovery work. It reads source files and VFP source containers without running Visual FoxPro, and writes SQLite indexes only under `~/.cache/foxpro-codebase-memory/indexes` unless `FOXPRO_MEMORY_CACHE` is set.

## GLM evidence review queue

`create_llm_review_plan` creates a deterministic queue that covers every indexed nonempty source unit and every graph edge exactly once. `run_llm_review_chunk` sends one bounded shard to the locally configured `claude -p` CLI with model `glm-5.3-flash`, then writes an `llm-review` evidence manifest beside the plan. `get_llm_review_status` and `run_llm_review_batch` make a full pass resumable; a batch runs at most 20 shards and applies the configured spend ceiling to each shard.

The configured provider receives the code in the selected shard. The reviewer produces only candidate relationships and evidence gaps: it cannot change a resolved edge, establish a runtime fact, or turn a dynamic VFP expression into a proven target. Import generated manifests with a new `index_repository` run together with the existing evidence manifests when the candidates need to appear in a graph generation.

Use C-like pseudocode only inside a reviewer explanation when it makes control flow easier to read. The canonical recovery artifact remains the provenance graph and original VFP source; the implementation destination is TypeScript.

The plugin is a companion to `codebase-memory-mcp`. It does not replace or modify the installed generic indexer.

## Indexed inputs

- `.prg`, `.mpr`, and `.h` source files
- Form, class-library, menu, report, and database-definition containers: `.scx/.sct`, `.vcx/.vct`, `.mnx/.mnt`, `.frx/.frt`, and `.dbc/.dct`
- Other files as explicit opaque coverage records

Form and library source retains original path, hash, record number, memo block/range, object identity, class metadata, and decoding warnings. Customer DBF rows are never decoded as application source.

## Graph layers

Version 0.2 adds a provenance-first graph on top of the original conservative symbol index:

- source artifacts, extracted units, form/class objects, symbols and statements;
- lexical syntax containment plus partial control-flow (`NEXT`, `BRANCH_OR_BODY`);
- lexical variable declarations, reads and writes; explicit `USE`, SQL and work-area data access observations;
- resolved and unresolved call, class, file and form references;
- imported observations from ReFox+, FoxLift, FoxBin2Prg and Profile Explorer/ETW.

Every node and edge retains a status, confidence and evidence record. `partial` means a lexical relationship is present but executable behavior is not proved; `unresolved` means the target must not be guessed. Runtime truth requires a trace observation.

## Using it

For Codex, add the GitHub marketplace and install the plugin:

```sh
codex plugin marketplace add martin-porman/FoxPRO
codex plugin add foxpro-codebase-memory@foxpro
```

Restart Codex or start a new task after installation. The plugin runs entirely locally with Python 3.10 or newer and no third-party Python packages.

Use the MCP tools:

1. `index_repository` with a source root, a lowercase project name, and optional `evidence_paths`.
2. `index_status`, `get_architecture`, and `check_index_coverage` before relying on results.
3. Use `search_graph`, `get_code_snippet`, and `trace_path` for the conservative source graph.
4. Use `query_graph`, `trace_graph`, and `get_evidence` for cross-layer source, extractor and trace evidence.

The equivalent command line interface is:

```sh
python3 scripts/foxpro-memory.py build /path/to/source --project my-project
python3 scripts/foxpro-memory.py search --project my-project --query methodname
```

Indexes are stored below `~/.cache/foxpro-codebase-memory/indexes/` by default. Use one project name per recovered source version. Keep a deployed artifact capture separate from an independently recovered source tree so a graph never silently combines different revisions.

## Limits

This is a conservative lexical structural parser. It resolves only proven lexical, file, or explicit-library relationships. Macro substitution, `EVALUATE`, unknown receivers, runtime code and ambiguous targets remain unresolved. A coverage result with no recorded issue is not a proof of runtime behavior or complete static resolution.

The main Windows binaries remain opaque when their container structure cannot be recovered. Their files and hashes are retained as coverage evidence rather than being claimed as indexed code.

## External evidence manifests

The plugin deliberately does not bundle or execute external decompilers, Windows tools, or ETW collection. They have separate licenses and platform requirements. Instead, pass a JSON manifest through `evidence_paths` when building an index. Supported adapters are `refox`, `foxlift`, `foxbin2prg`, `profile-explorer`, and `manual`.

```json
{
  "schema_version": 1,
  "adapter": "refox",
  "tool": {"name": "ReFox+", "version": "11.54"},
  "artifact": {"path": "/evidence/joosep5prg.exe", "sha256": "64-hex-sha256"},
  "observations": [
    {"key": "inventory", "kind": "ProgramInventory", "name": "main.fxp", "location": {"entrypoint": "main.fxp"}, "details": {"included_file_count": 1570}},
    {"key": "dynamic", "kind": "DynamicExpression", "name": "EVALUATE", "location": {}, "details": {"target": "unknown"}, "confidence": "unresolved"}
  ],
  "links": [{"source": "inventory", "target": "dynamic", "kind": "CONTAINS"}]
}
```

The artifact hash links a tool observation to the original file only when both the path and hash match an indexed artifact. A manifest can describe an unindexed artifact, but the graph marks it as a separate external artifact.
