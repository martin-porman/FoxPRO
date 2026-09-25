---
name: foxpro-memory
description: Index and explore Visual FoxPro programs, forms, class libraries, menus and reports with source provenance, call relationships and explicit unresolved references. Use for Joosep legacy analysis and planning its TypeScript migration.
---

# FoxPro codebase memory

Use this plugin's `foxpro_memory` MCP tools for VFP structure. The ordinary codebase-memory language index does not establish FoxPro coverage. This is a companion index, not a modification of the installed generic codebase-memory binary.

Start with `list_projects`, `index_status`, and `get_architecture`. Search symbols with `search_graph`, inspect exact source with `get_code_snippet`, and trace resolved links with `trace_path`. Use `query_graph`, `trace_graph`, and `get_evidence` to inspect statement/control/data layers and imported recovery or ETW evidence. Check `check_index_coverage` for source paths used in material conclusions. Paginate search results when `has_more` is true. The graph is a conservative structural index, not a full VFP compiler or a proof of runtime behavior.

Source identity includes the original path and, for a form/class method, its table record, object, memo field and source range. Keep separate projects for recovered application source and a different deployment/version. Do not combine two independently decompiled versions as if they were one codebase.

Dynamic macro expansion, EVALUATE, EXECSCRIPT, unresolved library paths and unknown receivers are evidence gaps. Report unresolved references; never turn a guessed target into a definite caller/callee. A clean extraction means readable input, not complete call resolution. Codepage assumptions, unsupported binary modules and partial extraction remain visible in coverage. External ReFox+, FoxLift, FoxBin2Prg and Profile Explorer results must enter as hash-bound evidence manifests; they are not silently merged into recovered source or treated as runtime truth without an ETW trace.

For this PC, the initial recovered source is `/home/martin/dev/Joosep5_decompiled`. Original Windows artifacts and their provenance are recorded under `/home/martin/dev/Joosep-Headless/analysis/original-evidence`. Consult the local plugin README and validation report for the current projects and original locations. The Headless business implementation is unverified and is not a behavior oracle.

Indexing reads sources and writes only the selected index/cache location. Keep customer database rows and credentials out of code indexes. Do not mount a running VM disk to find source; prefer its existing read-only file access. Original executable code is not executed by the indexer.

For TypeScript work, use the VFP graph to select a bounded workflow and find its dependencies. Establish behavior fixtures against a trusted legacy run before replacing business logic; then use ordinary codebase-memory for the new TypeScript code. Do not fabricate TypeScript wrappers simply to make the old source indexable.

When a whole-graph explanation pass is needed, call `create_llm_review_plan`, inspect `get_llm_review_status`, then use `run_llm_review_chunk` or the resumable 20-shard `run_llm_review_batch`. Those calls use the local `claude -p` configuration and default to `glm-5.3-flash`; code in each selected shard is sent to that configured provider. Review output is imported only as candidate or unresolved evidence, never as a proven relationship. A C-like description may help explain control flow, but the canonical source remains VFP evidence and the migration target remains TypeScript.

If the MCP tools have not appeared after installation, start a new task or use the plugin's documented local CLI to query the same persisted database.
