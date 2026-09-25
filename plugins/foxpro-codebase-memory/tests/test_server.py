import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ServerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'sample.prg').write_text('FUNCTION addone\nLPARAMETERS value\nRETURN value + 1\nENDFUNC\nFUNCTION caller\nRETURN addone(2)\nENDFUNC\n')
        self.before = (self.source / 'sample.prg').read_bytes()
        self.env = dict(os.environ, FOXPRO_MEMORY_CACHE=str(self.root / 'cache'))
        self.process = subprocess.Popen([sys.executable, str(ROOT / 'server.py')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        self.next_id = 0

    def tearDown(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        self.assertEqual(self.process.returncode, 0, self.process.stderr.read())
        self.process.stdout.close(); self.process.stderr.close()
        self.temp.cleanup()

    def request(self, method, params=None):
        self.next_id += 1
        self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': self.next_id, 'method': method, 'params': params or {}}) + '\n')
        self.process.stdin.flush()
        result = json.loads(self.process.stdout.readline())
        self.assertEqual(result['id'], self.next_id)
        return result

    def call(self, name, arguments=None):
        response = self.request('tools/call', {'name': name, 'arguments': arguments or {}})['result']
        self.assertFalse(response['isError'], response)
        return json.loads(response['content'][0]['text'])

    def test_stdio_build_query_and_provenance(self):
        for version in ('2024-11-05', '2025-03-26'):
            result = self.request('initialize', {'protocolVersion': version})['result']
            self.assertEqual(result['protocolVersion'], version)
        self.process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.process.stdin.flush()
        tools = self.request('tools/list')['result']['tools']
        self.assertIn('get_code_snippet', {t['name'] for t in tools})
        self.call('index_repository', {'source_root': str(self.source), 'project': 'fixture'})
        found = self.call('search_graph', {'project': 'fixture', 'query': 'addone'})
        self.assertEqual(found['total'], 1)
        symbol = found['results'][0]['id']
        snippet = self.call('get_code_snippet', {'project': 'fixture', 'symbol_id': symbol})
        self.assertIn('RETURN value + 1', snippet['code'])
        self.assertEqual(snippet['provenance']['absolute_path'], str(self.source / 'sample.prg'))
        self.assertEqual(len(snippet['provenance']['original_sha256']), 64)
        trace = self.call('trace_path', {'project': 'fixture', 'symbol_id': symbol, 'direction': 'inbound'})
        self.assertGreaterEqual(len(trace['edges']), 1)
        self.assertEqual(len(self.call('list_projects')['projects']), 1)
        coverage = self.call('check_index_coverage', {'project': 'fixture', 'paths': ['missing.prg']})
        self.assertEqual(coverage['missing_paths'], ['missing.prg'])
        self.assertFalse(coverage['complete'])
        self.assertEqual((self.source / 'sample.prg').read_bytes(), self.before)
        self.assertEqual(list(self.source.iterdir()), [self.source / 'sample.prg'])

    def test_bad_requests_do_not_kill_service(self):
        self.process.stdin.write('{broken\n[]\n'); self.process.stdin.flush()
        self.assertEqual(json.loads(self.process.stdout.readline())['error']['code'], -32700)
        self.assertEqual(json.loads(self.process.stdout.readline())['error']['code'], -32600)
        for name, args in [('index_status', {'project': '../escape'}), ('search_graph', {'limit': True}), ('unknown', {}), ('index_repository', {'source_root': str(self.source), 'extra_roots': 'wrong'}), ('trace_path', {'symbol_id': 'x', 'depth': 100})]:
            response = self.request('tools/call', {'name': name, 'arguments': args})
            self.assertTrue(response['result']['isError'])
        self.assertEqual(self.request('unknown')['error']['code'], -32601)
        self.assertEqual(self.request('ping')['result'], {})
        self.assertFalse((self.root / 'escape.sqlite').exists())

    def test_cli_uses_same_cache(self):
        command = [sys.executable, str(ROOT / 'scripts/foxpro-memory.py')]
        built = subprocess.run(command + ['build', str(self.source), '--project', 'cli-fixture'], env=self.env, capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        listed = subprocess.run(command + ['list'], env=self.env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(listed.stdout)['projects'][0]['project'], 'cli-fixture')
        bad = subprocess.run(command + ['status', '--project', '../bad'], env=self.env, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn('error', json.loads(bad.stdout))

if __name__ == '__main__':
    unittest.main()
