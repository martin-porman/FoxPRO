"""Versioned, read-only evidence-manifest loader for external recovery tools.

The plugin never runs FoxLift, FoxBin2Prg, a VM, or an ETW recorder itself.
Those tools have platform and licensing constraints.  Instead they produce a
small manifest whose observations are retained verbatim with hashes and tool
versions.  This prevents an extractor result from being mistaken for native
source or for a runtime-confirmed fact.
"""
from pathlib import Path
import hashlib
import json

MANIFEST_VERSION = 1
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
SUPPORTED_ADAPTERS = frozenset({'foxlift', 'foxbin2prg', 'profile-explorer', 'refox', 'manual'})


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _object(value, what):
    if not isinstance(value, dict):
        raise ValueError(f'{what} must be an object')
    return value


def _string(value, what, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f'{what} must be a nonempty string')
    return value


def load_manifests(paths):
    """Load explicit external-tool evidence without reading any VFP data rows."""
    observations = []
    links = []
    manifests = []
    for raw_path in paths or []:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Evidence manifest is not a regular file: {path}')
        raw = path.read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError(f'Evidence manifest exceeds {MAX_MANIFEST_BYTES} bytes: {path}')
        try:
            manifest = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f'Invalid evidence JSON: {path}: {exc}') from exc
        manifest = _object(manifest, 'Evidence manifest')
        if manifest.get('schema_version') != MANIFEST_VERSION:
            raise ValueError(f'Unsupported evidence schema_version in {path}; expected {MANIFEST_VERSION}')
        adapter = _string(manifest.get('adapter'), 'adapter').casefold()
        if adapter not in SUPPORTED_ADAPTERS:
            raise ValueError(f'Unsupported evidence adapter: {adapter}')
        tool = _object(manifest.get('tool'), 'tool')
        tool_name = _string(tool.get('name'), 'tool.name')
        tool_version = _string(tool.get('version'), 'tool.version')
        artifact = _object(manifest.get('artifact'), 'artifact')
        artifact_path = _string(artifact.get('path'), 'artifact.path')
        artifact_hash = _string(artifact.get('sha256'), 'artifact.sha256')
        if len(artifact_hash) != 64 or any(c not in '0123456789abcdefABCDEF' for c in artifact_hash):
            raise ValueError('artifact.sha256 must be a SHA-256 hex digest')
        identifier = _digest(raw)
        manifests.append({'path': str(path), 'sha256': identifier, 'adapter': adapter, 'tool_name': tool_name, 'tool_version': tool_version, 'artifact_path': artifact_path, 'artifact_sha256': artifact_hash, 'details': manifest.get('details', {})})
        raw_observations = manifest.get('observations', [])
        if not isinstance(raw_observations, list):
            raise ValueError('observations must be an array')
        known = set()
        for index, item in enumerate(raw_observations):
            item = _object(item, f'observations[{index}]')
            key = _string(item.get('key'), f'observations[{index}].key')
            if key in known:
                raise ValueError(f'Duplicate observation key in {path}: {key}')
            known.add(key)
            observations.append({'manifest_sha256': identifier, 'key': key, 'kind': _string(item.get('kind'), f'observations[{index}].kind'), 'name': _string(item.get('name'), f'observations[{index}].name'), 'location': item.get('location', {}), 'details': item.get('details', {}), 'confidence': item.get('confidence', 'observed')})
        raw_links = manifest.get('links', [])
        if not isinstance(raw_links, list):
            raise ValueError('links must be an array')
        for index, item in enumerate(raw_links):
            item = _object(item, f'links[{index}]')
            source = _string(item.get('source'), f'links[{index}].source')
            target = _string(item.get('target'), f'links[{index}].target')
            if source not in known or target not in known:
                raise ValueError(f'links[{index}] references an unknown observation key')
            links.append({'manifest_sha256': identifier, 'source': source, 'target': target, 'kind': _string(item.get('kind'), f'links[{index}].kind'), 'details': item.get('details', {}), 'confidence': item.get('confidence', 'observed')})
    return {'manifests': manifests, 'observations': observations, 'links': links}
