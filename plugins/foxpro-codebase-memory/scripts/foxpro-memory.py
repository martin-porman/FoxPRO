#!/usr/bin/env python3
"""Command-line access to the same services as the MCP tools."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import invoke

def main():
    parser = argparse.ArgumentParser(description='Native Visual FoxPro codebase memory; originals stay read-only.')
    sub = parser.add_subparsers(dest='command', required=True)
    names = {'build': 'index_repository', 'list': 'list_projects', 'status': 'index_status', 'architecture': 'get_architecture', 'search': 'search_graph', 'trace': 'trace_path', 'snippet': 'get_code_snippet', 'coverage': 'check_index_coverage'}
    for command in names:
        p = sub.add_parser(command)
        if command != 'list':
            p.add_argument('--project', default='joosep')
        if command == 'build':
            p.add_argument('source_root'); p.add_argument('--extra-root', dest='extra_roots', action='append')
        if command == 'search':
            p.add_argument('--query'); p.add_argument('--name-pattern'); p.add_argument('--label'); p.add_argument('--path'); p.add_argument('--limit', type=int, default=100); p.add_argument('--offset', type=int, default=0)
        if command in {'trace', 'snippet'}:
            p.add_argument('symbol_id')
        if command == 'trace':
            p.add_argument('--direction', choices=['inbound', 'outbound', 'both'], default='outbound'); p.add_argument('--depth', type=int, default=2)
        if command == 'coverage':
            p.add_argument('--path', dest='paths', action='append'); p.add_argument('--scope', dest='scopes', action='append')
    args = vars(parser.parse_args()); command = args.pop('command')
    try:
        result = invoke(names[command], {k: v for k, v in args.items() if v is not None})
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False)); return 1
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0

if __name__ == '__main__':
    sys.exit(main())
