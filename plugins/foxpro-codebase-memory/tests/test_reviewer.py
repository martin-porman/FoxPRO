import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from foxpro_memory.index import build_index
from foxpro_memory.review import build_prompt, parse_model_content, review_manifest
from foxpro_memory.reviewer import create_review_plan, review_plan_status, run_claude_review_chunk


class ReviewerTests(unittest.TestCase):
    def test_plan_covers_every_source_unit_and_graph_edge(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'source';root.mkdir()
            (root/'main.prg').write_text('FUNCTION Main\nRETURN helper()\nENDFUNC\nFUNCTION helper\nRETURN 1\nENDFUNC\n')
            db=Path(temporary)/'fixture.sqlite';build_index(root,db,project='fixture')
            result=create_review_plan(db,'fixture','Explain this safely',Path(temporary)/'reviews',max_source_chars=20,max_edges=100)
            plan=json.loads(Path(result['plan_path']).read_text())
            self.assertEqual(plan['coverage']['units_total'],1)
            self.assertEqual(sum(len(chunk.get('unit_ids',[])) for chunk in plan['chunks']),1)
            self.assertEqual(sum(len(chunk.get('edge_ids',[])) for chunk in plan['chunks']),plan['coverage']['graph_edges_total'])
            status=review_plan_status(result['plan_path'])
            self.assertEqual(status['pending_chunks'],result['pending_chunks'])

    def test_llm_manifest_cannot_be_observed(self):
        graph_slice={'units':[{'id':'unit-1','unit_key':'main.prg','owner':'','provenance':{},'source':'FUNCTION Main\nRETURN 1\nENDFUNC'}],'nodes':[{'id':'source','kind':'Function','name':'source'},{'id':'target','kind':'Function','name':'target'}],'edges':[],'limitations':'test'}
        prompt=build_prompt(graph_slice,'What is the relation?')
        self.assertEqual({node['id'] for node in prompt['graph_slice']['nodes']},{'source','target'})
        self.assertEqual(prompt['graph_slice']['units'][0]['source'],'FUNCTION Main\nRETURN 1\nENDFUNC')
        response=parse_model_content('''```json
{"summary":"possible call","candidates":[{"key":"call","kind":"CALLS","name":"source to target","source_node_ids":["source"],"target_node_ids":["target"],"rationale":"name match","missing_evidence":"runtime trace"}],"gaps":[]}
```''')
        manifest=review_manifest(response,artifact_path='/plan.json',artifact_sha256='a'*64,tool_version='glm-5.3-flash',question='What is the relation?',known_node_ids={'source','target'})
        self.assertEqual(manifest['adapter'],'llm-review')
        self.assertEqual(manifest['observations'][0]['confidence'],'candidate')
        self.assertIn('promotion_rule',manifest['details'])

    def test_large_review_prompt_uses_standard_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'source';root.mkdir()
            (root/'main.prg').write_text('FUNCTION Main\nRETURN 1\nENDFUNC\n')
            db=Path(temporary)/'fixture.sqlite';build_index(root,db,project='fixture')
            plan=create_review_plan(db,'fixture','Explain safely',Path(temporary)/'reviews',max_source_chars=20000,max_edges=100)
            output='{"result":"{\\"summary\\":\\"gap\\",\\"candidates\\":[],\\"gaps\\":[{\\"key\\":\\"unknown\\",\\"name\\":\\"Unknown\\",\\"rationale\\":\\"need trace\\",\\"missing_evidence\\":\\"trace\\"}]}"}'
            with patch('foxpro_memory.reviewer.subprocess.run') as run:
                run.return_value.returncode=0;run.return_value.stdout=output;run.return_value.stderr=''
                result=run_claude_review_chunk(plan['plan_path'],'code-00001')
            self.assertEqual(result['gap_count'],1)
            self.assertNotIn('Review request:',run.call_args.args[0])
            self.assertIn('Review request:',run.call_args.kwargs['input'])


if __name__=='__main__':
    unittest.main()
