# FoxPRO

Open tools for understanding and modernizing Visual FoxPro applications.

## FoxPro Codebase Memory

[`foxpro-codebase-memory`](plugins/foxpro-codebase-memory/) is a local MCP plugin for Codex. It indexes `.prg`, `.mpr`, and `.h` source, plus VFP form, class-library, menu, report, and database-definition containers. It retains original-file hashes and source-container memo provenance, and reports uncertain dynamic references instead of guessing.

Install from this repository:

```sh
codex plugin marketplace add martin-porman/FoxPRO
codex plugin add foxpro-codebase-memory@foxpro
```

It requires Python 3.10 or later and uses only the Python standard library. See the [plugin README](plugins/foxpro-codebase-memory/README.md) for commands, supported inputs, and limits.

## Development

Run the plugin validation and test suite from the plugin directory:

```sh
cd plugins/foxpro-codebase-memory
python3 /home/martin/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
python3 -m unittest discover -s tests -v
```

The indexer does not execute Visual FoxPro programs or read business DBF rows as source code.
