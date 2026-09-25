"""Constrained LLM review artifacts for FoxPro recovery evidence.

An LLM may identify a useful hypothesis, but it cannot turn an unresolved
macro, receiver, or business workflow into a proven edge.  This module builds
the narrow prompt and validates the model's JSON before it becomes a graph
manifest.  Every generated observation stays `candidate` or `unresolved`.
"""
import json
from datetime import datetime, timezone


SYSTEM_PROMPT = """You review Visual FoxPro recovery evidence. Return JSON only.
Never state a relationship as proven. Use source_node_ids and target_node_ids
only when they appear in the supplied graph slice. A candidate must explain
what exact additional source, extractor output, or runtime trace would prove
or reject it. Do not copy database rows, secrets, or unrelated source text.
Schema: {summary: string, candidates: [{key, kind, name, source_node_ids,
target_node_ids, rationale, missing_evidence}], gaps: [{key, name, rationale,
missing_evidence}]}."""


def build_prompt(graph_slice, question):
    """Provide the source and graph evidence needed to review this bounded shard."""
    return {
        'question': question,
        'graph_slice': {
            # Provenance is preserved in the local evidence graph and final
            # manifest.  It is intentionally excluded from provider prompts:
            # absolute paths and extraction metadata can dominate a small
            # source shard without helping a model assess its logic.
            'units': [{'id': unit['id'], 'unit_key': unit['unit_key'], 'owner': unit['owner'], 'source': unit['source']} for unit in graph_slice.get('units', [])],
            'nodes': [{'id': node['id'], 'kind': node['kind'], 'name': node['name'], 'start_line': node.get('start_line'), 'end_line': node.get('end_line'), 'evidence_id': node.get('evidence_id')} for node in graph_slice['nodes']],
            'edges': [{'source_id': edge['source_id'], 'target_id': edge['target_id'], 'kind': edge['kind'], 'status': edge['status'], 'confidence': edge['confidence'], 'evidence_id': edge.get('evidence_id')} for edge in graph_slice['edges']],
            'limitations': graph_slice.get('limitations'),
        },
    }


def _string(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonempty string')
    return value


def _ids(value, field, known):
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or item not in known for item in value):
        raise ValueError(f'{field} must contain known graph node IDs')
    return value


def review_manifest(response, *, artifact_path, artifact_sha256, tool_version, question, known_node_ids, captured_at=None):
    """Validate a GLM response and convert it to an importable safe manifest."""
    if not isinstance(response, dict):
        raise ValueError('LLM response must be a JSON object')
    observations = []
    known = set(known_node_ids)
    for index, candidate in enumerate(response.get('candidates', [])):
        if not isinstance(candidate, dict):
            raise ValueError(f'candidates[{index}] must be an object')
        key = 'candidate-' + _string(candidate.get('key'), f'candidates[{index}].key')
        source_ids = _ids(candidate.get('source_node_ids'), f'candidates[{index}].source_node_ids', known)
        target_ids = _ids(candidate.get('target_node_ids'), f'candidates[{index}].target_node_ids', known)
        observations.append({'key': key, 'kind': 'CandidateRelationship', 'name': _string(candidate.get('name'), f'candidates[{index}].name'), 'location': {'question': question}, 'details': {'candidate_kind': _string(candidate.get('kind'), f'candidates[{index}].kind'), 'source_node_ids': source_ids, 'target_node_ids': target_ids, 'rationale': _string(candidate.get('rationale'), f'candidates[{index}].rationale'), 'missing_evidence': _string(candidate.get('missing_evidence'), f'candidates[{index}].missing_evidence')}, 'confidence': 'candidate'})
    for index, gap in enumerate(response.get('gaps', [])):
        if not isinstance(gap, dict):
            raise ValueError(f'gaps[{index}] must be an object')
        observations.append({'key': 'gap-' + _string(gap.get('key'), f'gaps[{index}].key'), 'kind': 'EvidenceGap', 'name': _string(gap.get('name'), f'gaps[{index}].name'), 'location': {'question': question}, 'details': {'rationale': _string(gap.get('rationale'), f'gaps[{index}].rationale'), 'missing_evidence': _string(gap.get('missing_evidence'), f'gaps[{index}].missing_evidence')}, 'confidence': 'unresolved'})
    if not observations:
        raise ValueError('LLM response contains neither candidates nor gaps')
    return {'schema_version': 1, 'adapter': 'llm-review', 'tool': {'name': 'GLM-5.3-Flash evidence reviewer', 'version': tool_version}, 'artifact': {'path': artifact_path, 'sha256': artifact_sha256}, 'details': {'question': question, 'summary': _string(response.get('summary'), 'summary'), 'captured_at': captured_at or datetime.now(timezone.utc).isoformat(), 'promotion_rule': 'LLM observations are hypotheses only; a source or trace must independently prove a relationship.'}, 'observations': observations, 'links': []}


def parse_model_content(content):
    """Accept a JSON object or a Markdown fenced JSON object, nothing else."""
    if not isinstance(content, str):
        raise ValueError('Model response content must be text')
    value = content.strip()
    if value.startswith('```') and value.endswith('```'):
        value = value.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Model response is not valid JSON: {exc}') from exc
