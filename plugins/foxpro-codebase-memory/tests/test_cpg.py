import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from foxpro_memory.index import Graph, build_index
from foxpro_memory.parser import lexical_cpg


class CpgTests(unittest.TestCase):
    def test_lexical_cpg_keeps_dynamic_receiver_unresolved(self):
        result = lexical_cpg('FUNCTION Main\nLOCAL total\ntotal = 1\nIF total > 0\n  USE customers\n  this.save(total)\nENDIF\nRETURN total\nENDFUNC\n')
        self.assertEqual(result['version'], 'vfp-lexical-cpg-2.0.0')
        self.assertTrue(any(edge['kind'] == 'BRANCH_OR_BODY' for edge in result['control_edges']))
        self.assertIn({'statement_key': '5:5:4', 'name': 'customers', 'operation': 'OPEN', 'line': 5, 'confidence': 'observed'}, result['data_accesses'])
        self.assertTrue(any(item['name'] == 'this.save' and item['confidence'] == 'unresolved' for item in result['variables']))

    def test_external_evidence_is_a_distinct_graph_layer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'; root.mkdir()
            source = root / 'main.prg'
            source.write_text('FUNCTION Main\nLOCAL total\ntotal = 1\nUSE customers\nRETURN total\nENDFUNC\n')
            manifest_path = Path(temporary) / 'refox.json'
            manifest_path.write_text(json.dumps({
                'schema_version': 1,
                'adapter': 'refox',
                'tool': {'name': 'ReFox+', 'version': '11.54'},
                'artifact': {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()},
                'observations': [
                    {'key': 'inventory', 'kind': 'IncludedFileSet', 'name': 'main.fxp inventory', 'location': {'entrypoint': 'main.fxp'}, 'details': {'included_file_count': 2}},
                    {'key': 'dynamic', 'kind': 'DynamicExpression', 'name': 'EVALUATE', 'location': {'included_file': 'main.fxp'}, 'details': {'target': 'unknown'}, 'confidence': 'unresolved'},
                ],
                'links': [{'source': 'inventory', 'target': 'dynamic', 'kind': 'CONTAINS'}],
            }))
            db = Path(temporary) / 'graph.sqlite'
            status = build_index(root, db, project='fixture', evidence_paths=[manifest_path])
            self.assertEqual(status['schema_version'], '2')
            self.assertGreater(status['counts']['graph_nodes'], status['counts']['symbols'])
            graph = Graph(db)
            observed = graph.query_graph(kind='External:IncludedFileSet')
            self.assertEqual(observed['total_nodes'], 1)
            evidence = graph.get_evidence(observed['nodes'][0]['id'])
            self.assertEqual(evidence['evidence'][0]['tool'], 'ReFox+')
            traced = graph.trace_graph(observed['nodes'][0]['id'])
            self.assertTrue(any(edge['kind'] == 'CONTAINS' for edge in traced['edges']))
            data = graph.query_graph(edge_kind='DATA_OPEN')
            self.assertEqual(data['total_edges'], 1)


if __name__ == '__main__':
    unittest.main()
