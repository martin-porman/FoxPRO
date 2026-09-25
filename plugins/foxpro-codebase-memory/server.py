#!/usr/bin/env python3
"""Dependency-free, newline-delimited JSON-RPC MCP service for VFP graphs."""
import contextlib
import json
import os
from pathlib import Path
import re
import sys

VERSION = '0.1.0'
PROTOCOLS = ('2024-11-05', '2025-03-26')
STR = {'type': 'string'}
PROJECT = {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9_-]{0,63}$', 'default': 'joosep'}

def tool(name, description, properties=None, required=()):
    return {'name': name, 'description': description, 'inputSchema': {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False}}

TOOLS = [
    tool('index_repository', 'Build a native Visual FoxPro index; originals are read-only. Writes only the selected project cache. Dynamic references stay unresolved.', {'project': PROJECT, 'source_root': STR, 'extra_roots': {'type': 'array', 'items': STR}}, ('source_root',)),
    tool('list_projects', 'List locally cached Visual FoxPro projects and index generations.'),
    tool('index_status', 'Show generation, original roots, counts and coverage limitations.', {'project': PROJECT}),
    tool('get_architecture', 'Summarize indexed symbol/file/reference types and roots; this is a structural inventory, not inferred business architecture.', {'project': PROJECT}),
    tool('search_graph', 'Find native VFP symbols. Paginate until has_more is false when requiring exhaustive results.', {'project': PROJECT, 'query': STR, 'name_pattern': STR, 'label': STR, 'path': STR, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000}, 'offset': {'type': 'integer', 'minimum': 0}}),
    tool('trace_path', 'Trace statically resolved references and report unresolved calls separately.', {'project': PROJECT, 'symbol_id': STR, 'direction': {'type': 'string', 'enum': ['inbound', 'outbound', 'both']}, 'depth': {'type': 'integer', 'minimum': 0, 'maximum': 8}}, ('symbol_id',)),
    tool('get_code_snippet', 'Read original VFP symbol text with file hash and record/memo provenance.', {'project': PROJECT, 'symbol_id': STR}, ('symbol_id',)),
    tool('check_index_coverage', 'Inspect input files, extraction failures and parser gaps before relying on graph evidence.', {'project': PROJECT, 'paths': {'type': 'array', 'items': STR}, 'scopes': {'type': 'array', 'items': STR}}),
]

def cache_root():
    return Path(os.environ.get('FOXPRO_MEMORY_CACHE', str(Path.home() / '.cache/foxpro-codebase-memory/indexes'))).expanduser().resolve()

def project_path(project):
    if not isinstance(project, str) or re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', project) is None:
        raise ValueError('project must be 1–64 lowercase letters, digits, underscores or hyphens; start with a letter/digit')
    path = cache_root() / (project + '.sqlite')
    if path.is_symlink():
        raise ValueError('Project cache must not be a symbolic link')
    return path

def validate(name, arguments):
    if not isinstance(arguments, dict):
        raise ValueError('Tool arguments must be an object')
    definition = next((t for t in TOOLS if t['name'] == name), None)
    if definition is None:
        raise ValueError('Unknown tool: ' + str(name))
    schema = definition['inputSchema']
    for key in schema['required']:
        if key not in arguments:
            raise ValueError('Missing required argument: ' + key)
    for key, value in arguments.items():
        if key not in schema['properties']:
            raise ValueError('Unknown argument: ' + key)
        rule = schema['properties'][key]
        kind = rule['type']
        if kind == 'string' and (not isinstance(value, str) or not value.strip()):
            raise ValueError(key + ' must be a nonempty string')
        if kind == 'integer' and (type(value) is not int or value < rule.get('minimum', value) or value > rule.get('maximum', value)):
            raise ValueError(key + ' is outside the accepted integer range')
        if kind == 'array' and (not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value)):
            raise ValueError(key + ' must be an array of nonempty strings')
        if 'enum' in rule and value not in rule['enum']:
            raise ValueError(key + ' has an unsupported value')
    return dict(arguments)

def invoke(name, arguments):
    args = validate(name, arguments)
    # Lazy import keeps initialize/tool discovery available even if an index is absent.
    from foxpro_memory.index import Graph, build_index
    if name == 'list_projects':
        results = []
        for path in sorted(cache_root().glob('*.sqlite')):
            try:
                checked = project_path(path.stem)
                results.append({'project': path.stem, 'index': str(checked), 'status': Graph(checked).index_status()})
            except Exception as exc:
                results.append({'project': path.stem, 'error': str(exc)})
        return {'projects': results}
    project = args.pop('project', 'joosep')
    path = project_path(project)
    if name == 'index_repository':
        return build_index(args['source_root'], path, project=project, extra_roots=args.get('extra_roots'))
    if not path.is_file():
        raise ValueError('Project is not indexed: ' + project + '. Use index_repository first.')
    graph = Graph(path)
    return getattr(graph, 'index_status' if name == 'get_architecture' else name)(**args)

def error(identifier, code, message):
    return {'jsonrpc': '2.0', 'id': identifier, 'error': {'code': code, 'message': message}}

def handle(request):
    if not isinstance(request, dict):
        return error(None, -32600, 'Invalid Request')
    identifier = request.get('id')
    if request.get('jsonrpc') != '2.0' or not isinstance(request.get('method'), str) or isinstance(identifier, (dict, list, bool)):
        return error(identifier if isinstance(identifier, (str, int)) and not isinstance(identifier, bool) else None, -32600, 'Invalid Request')
    method = request['method']
    # Notifications (including initialized/cancelled) never receive responses.
    if 'id' not in request:
        return None
    params = request.get('params', {})
    if not isinstance(params, dict):
        return error(identifier, -32602, 'Params must be an object')
    if method == 'initialize':
        requested = params.get('protocolVersion')
        result = {'protocolVersion': requested if requested in PROTOCOLS else PROTOCOLS[-1], 'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': {'name': 'foxpro-codebase-memory', 'version': VERSION}, 'instructions': 'Native Visual FoxPro structural evidence. Check coverage and unresolved references; this is not a compiler or runtime proof.'}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': TOOLS}
    elif method == 'tools/call':
        if not isinstance(params.get('name'), str):
            return error(identifier, -32602, 'Tool name is required')
        try:
            with contextlib.redirect_stdout(sys.stderr):
                value = invoke(params['name'], params.get('arguments', {}))
            result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}], 'isError': False}
        except Exception as exc:
            result = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
    else:
        return error(identifier, -32601, 'Method not found')
    return {'jsonrpc': '2.0', 'id': identifier, 'result': result}

def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except (ValueError, UnicodeError):
            response = error(None, -32700, 'Parse error')
        else:
            try:
                response = handle(request)
            except Exception:
                response = error(request.get('id') if isinstance(request, dict) else None, -32603, 'Internal error')
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
