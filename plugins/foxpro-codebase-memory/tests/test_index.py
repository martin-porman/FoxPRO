import tempfile
import unittest
from pathlib import Path
from foxpro_memory.index import build_index,Graph

class IndexTest(unittest.TestCase):
    def test_determinism_deletion_and_source(self):
        with tempfile.TemporaryDirectory() as root:
            source=Path(root)/'source';source.mkdir();db=Path(root)/'cache.sqlite';p=source/'main.prg';p.write_text('FUNCTION Main\n helper()\nENDFUNC\nFUNCTION Helper\n RETURN 1\nENDFUNC\n')
            a=build_index(source,db);b=build_index(source,db);self.assertEqual(a['graph_fingerprint'],b['graph_fingerprint']);g=Graph(db)
            main=g.search_graph(query='Main')['results'][0];self.assertIn('FUNCTION Main',g.get_code_snippet(main['id'])['code']);self.assertEqual(len(g.trace_path(main['id'])['edges']),1)
            p.unlink();build_index(source,db);self.assertEqual(Graph(db).search_graph()['total'],0)
    def test_no_global_or_dynamic_guess(self):
        with tempfile.TemporaryDirectory() as root:
            source=Path(root)/'source';source.mkdir();db=Path(root)/'cache.sqlite'
            (source/'a.prg').write_text('FUNCTION Main\n this.helper()\n helper()\nENDFUNC\n');(source/'b.prg').write_text('FUNCTION Helper\nENDFUNC\n');build_index(source,db)
            g=Graph(db);main=g.search_graph(query='Main')['results'][0];trace=g.trace_path(main['id']);self.assertEqual(trace['edges'],[]);self.assertTrue(trace['unresolved'])
    def test_output_root_guard(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):build_index(root,Path(root)/'db.sqlite')
    def test_unknown_file_coverage(self):
        with tempfile.TemporaryDirectory() as root:
            source=Path(root)/'source';source.mkdir();(source/'app.exe').write_bytes(b'MZ');db=Path(root)/'cache.sqlite';build_index(source,db)
            self.assertFalse(Graph(db).check_index_coverage(paths=['app.exe'])['complete'])
