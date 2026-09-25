"""Deterministic Claude/GLM review queue for a persisted evidence graph."""
from pathlib import Path
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import uuid
import tempfile
import uuid
from .review import SYSTEM_PROMPT, build_prompt, parse_model_content, review_manifest

REVIEW_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['summary', 'candidates', 'gaps'],
    'properties': {
        'summary': {'type': 'string'},
        'candidates': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['key', 'kind', 'name', 'source_node_ids', 'target_node_ids', 'rationale', 'missing_evidence'], 'properties': {'key': {'type': 'string'}, 'kind': {'type': 'string'}, 'name': {'type': 'string'}, 'source_node_ids': {'type': 'array', 'items': {'type': 'string'}}, 'target_node_ids': {'type': 'array', 'items': {'type': 'string'}}, 'rationale': {'type': 'string'}, 'missing_evidence': {'type': 'string'}}}},
        'gaps': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['key', 'name', 'rationale', 'missing_evidence'], 'properties': {'key': {'type': 'string'}, 'name': {'type': 'string'}, 'rationale': {'type': 'string'}, 'missing_evidence': {'type': 'string'}}}},
    },
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _read_metadata(db_path):
    with sqlite3.connect(db_path) as db:
        return {key: json.loads(value) for key, value in db.execute('SELECT key,value FROM metadata')}


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _read_claude_envelope(path):
    raw=Path(path).read_bytes()
    for encoding in ('utf-8-sig','utf-16'):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError,json.JSONDecodeError):
            pass
    raise ValueError('Claude output is not valid UTF-8 or UTF-16 JSON')


def create_review_plan(db_path, project, question, output_root, max_source_chars=4000, max_edges=25):
    """Queue every nonempty source unit and every graph edge exactly once."""
    # Model response time depends on the surrounding node metadata as well as
    # the source text.  Keep default slices small enough to recover and resume
    # reliably from a VM worker.
    max_source_chars=max(2000, min(int(max_source_chars), 1000000))
    max_edges=max(10, min(int(max_edges), 20000))
    metadata=_read_metadata(db_path)
    if metadata.get('project') != project:
        raise ValueError('Review project does not match graph database metadata')
    with sqlite3.connect(db_path) as db:
        units=list(db.execute('SELECT id,length(source) FROM units ORDER BY file_id,unit_key'))
        edges=[row[0] for row in db.execute('SELECT id FROM graph_edges ORDER BY id')]
    code_chunks=[];current=[];size=0;empty_units=0
    for unit_id, length in units:
        if not length:
            empty_units+=1
            continue
        if current and size + length > max_source_chars:
            code_chunks.append(current);current=[];size=0
        current.append(unit_id);size+=length
    if current:code_chunks.append(current)
    chunks=[]
    for index, unit_ids in enumerate(code_chunks, 1):
        chunks.append({'id':f'code-{index:05d}','kind':'code','unit_ids':unit_ids})
    for index in range(0,len(edges),max_edges):
        chunks.append({'id':f'links-{index//max_edges+1:05d}','kind':'links','edge_ids':edges[index:index+max_edges]})
    plan={'schema_version':1,'project':project,'graph_path':str(Path(db_path).resolve()),'graph_fingerprint':metadata['graph_fingerprint'],'question':question,'max_source_chars':max_source_chars,'max_edges':max_edges,'coverage':{'units_total':len(units),'empty_units':empty_units,'nonempty_units':len(units)-empty_units,'graph_edges_total':len(edges),'code_chunks':len(code_chunks),'link_chunks':(len(edges)+max_edges-1)//max_edges},'chunks':chunks}
    encoded=_json(plan).encode('utf-8');plan['plan_sha256']=_sha256_bytes(encoded)
    root=Path(output_root).expanduser().resolve()/project/plan['plan_sha256']
    root.mkdir(parents=True,exist_ok=True)
    path=root/'plan.json';path.write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    return {'plan_path':str(path),'plan_sha256':plan['plan_sha256'],'coverage':plan['coverage'],'pending_chunks':len(chunks)}


def _placeholders(count):
    return ','.join('?' for _ in range(count))


def _chunk_slice(plan, chunk):
    db_path=plan['graph_path']
    if _read_metadata(db_path).get('graph_fingerprint') != plan['graph_fingerprint']:
        raise ValueError('Graph changed after review plan creation; create a new plan')
    with sqlite3.connect(db_path) as db:
        db.row_factory=sqlite3.Row
        if chunk['kind']=='code':
            unit_ids=chunk['unit_ids'];marks=_placeholders(len(unit_ids))
            units=[dict(row) for row in db.execute(f'SELECT id,file_id,unit_key,owner,provenance,source FROM units WHERE id IN ({marks}) ORDER BY file_id,unit_key',unit_ids)]
            nodes=[dict(row) for row in db.execute(f'SELECT * FROM graph_nodes WHERE unit_id IN ({marks}) ORDER BY start_line,id',unit_ids)]
            return {'nodes':nodes,'edges':[],'units':[{'id':unit['id'],'unit_key':unit['unit_key'],'owner':unit['owner'],'provenance':json.loads(unit['provenance']),'source':unit['source']} for unit in units],'limitations':'Code shard. All graph edges are reviewed separately in link shards.'}
        edge_ids=chunk['edge_ids'];marks=_placeholders(len(edge_ids))
        edges=[dict(row) for row in db.execute(f'SELECT * FROM graph_edges WHERE id IN ({marks}) ORDER BY id',edge_ids)]
        node_ids=sorted({edge['source_id'] for edge in edges}|{edge['target_id'] for edge in edges})
        node_marks=_placeholders(len(node_ids))
        nodes=[dict(row) for row in db.execute(f'SELECT * FROM graph_nodes WHERE id IN ({node_marks}) ORDER BY id',node_ids)] if node_ids else []
        return {'nodes':nodes,'edges':edges,'units':[],'limitations':'Link shard. Node source is reviewed separately in code shards.'}


def run_claude_review_chunk(plan_path, chunk_id, *, model='glm-5.3-flash', max_budget_usd=0.25, timeout_seconds=900):
    """Run one pre-planned chunk through the caller's configured Claude/Z.ai CLI."""
    plan_path=Path(plan_path).expanduser().resolve();plan=json.loads(plan_path.read_text())
    if plan.get('schema_version') != 1:raise ValueError('Unsupported review plan schema')
    chunk=next((item for item in plan['chunks'] if item['id']==chunk_id),None)
    if chunk is None:raise ValueError('Review chunk not found')
    graph_slice=_chunk_slice(plan,chunk)
    known={node['id'] for node in graph_slice['nodes']}
    if not known:raise ValueError('Review chunk has no graph nodes')
    request=build_prompt(graph_slice,plan['question']);request['chunk']={'id':chunk_id,'kind':chunk['kind'],'coverage_rule':'Review all supplied records. Return hypotheses and evidence gaps only.'}
    prompt=SYSTEM_PROMPT+'\n\nReview request:\n'+json.dumps(request,ensure_ascii=False)
    command=['claude','-p','--no-session-persistence','--output-format','json','--json-schema',json.dumps(REVIEW_SCHEMA,separators=(',',':')),'--model',model,'--max-budget-usd',str(max_budget_usd)]
    completed=subprocess.run(command,input=prompt,text=True,capture_output=True,timeout=int(timeout_seconds),check=False)
    if completed.returncode != 0:
        detail=(completed.stderr or completed.stdout).strip()[-2000:]
        raise RuntimeError(f'Claude review failed for {chunk_id}: {detail}')
    envelope=json.loads(completed.stdout)
    content=envelope.get('result') if isinstance(envelope,dict) else None
    response=parse_model_content(content)
    manifest=review_manifest(response,artifact_path=str(plan_path),artifact_sha256=plan['plan_sha256'],tool_version=model,question=plan['question'],known_node_ids=known)
    manifest['details'].update({'review_chunk':chunk_id,'review_kind':chunk['kind'],'graph_project':plan['project'],'graph_fingerprint':plan['graph_fingerprint']})
    output=plan_path.parent/'manifests';output.mkdir(exist_ok=True)
    manifest_path=output/(chunk_id+'.json');manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return {'chunk_id':chunk_id,'manifest_path':str(manifest_path),'candidate_count':len(response.get('candidates',[])),'gap_count':len(response.get('gaps',[])),'model':model}


def review_plan_status(plan_path):
    """Return deterministic queue state without reading provider credentials."""
    plan_path=Path(plan_path).expanduser().resolve()
    plan=json.loads(plan_path.read_text())
    completed={path.stem for path in (plan_path.parent/'manifests').glob('*.json')} if (plan_path.parent/'manifests').is_dir() else set()
    chunks=[{'id':chunk['id'],'kind':chunk['kind'],'state':'complete' if chunk['id'] in completed else 'pending'} for chunk in plan['chunks']]
    return {'project':plan['project'],'plan_path':str(plan_path),'coverage':plan['coverage'],'chunks':chunks,'completed_chunks':len(completed & {chunk['id'] for chunk in plan['chunks']}),'pending_chunks':len([chunk for chunk in plan['chunks'] if chunk['id'] not in completed])}


def run_claude_review_batch(plan_path, *, max_chunks=1, model='glm-5.3-flash', max_budget_usd=0.25, timeout_seconds=900):
    """Run the next bounded pending review shards; callers control total spend."""
    max_chunks=max(1,min(int(max_chunks),20))
    status=review_plan_status(plan_path)
    pending=[chunk['id'] for chunk in status['chunks'] if chunk['state']=='pending'][:max_chunks]
    results=[]
    for chunk_id in pending:
        results.append(run_claude_review_chunk(plan_path,chunk_id,model=model,max_budget_usd=max_budget_usd,timeout_seconds=timeout_seconds))
    return {'results':results,'status':review_plan_status(plan_path)}


def install_vm_worker(ssh_target, remote_root):
    """Install a credential-isolated PowerShell runner into a Windows VM."""
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    source=scripts/'vm-glm-worker.ps1';starter=scripts/'start-vm-glm-review.ps1'
    if not source.is_file() or not starter.is_file():raise ValueError('VM worker scripts are missing from this plugin')
    remote_root=remote_root.rstrip('/\\')
    setup=['ssh',ssh_target,'powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-Command',f"New-Item -ItemType Directory -Force -Path '{remote_root}/inbox'; New-Item -ItemType Directory -Force -Path '{remote_root}/out'"]
    completed=subprocess.run(setup,text=True,capture_output=True,timeout=60,check=False)
    if completed.returncode:raise RuntimeError('Unable to create VM worker directories: '+(completed.stderr or completed.stdout).strip()[-1000:])
    copied=subprocess.run(['scp',str(source),str(starter),f'{ssh_target}:{remote_root}/'],text=True,capture_output=True,timeout=60,check=False)
    if copied.returncode:raise RuntimeError('Unable to copy VM worker script: '+(copied.stderr or copied.stdout).strip()[-1000:])
    return {'ssh_target':ssh_target,'remote_root':remote_root,'runner_path':remote_root+'/run-review.ps1','starter_path':remote_root+'/start-vm-glm-review.ps1'}


def _vm_review_request(plan_path, chunk_id):
    plan_path=Path(plan_path).expanduser().resolve();plan=json.loads(plan_path.read_text())
    chunk=next((item for item in plan['chunks'] if item['id']==chunk_id),None)
    if chunk is None:raise ValueError('Review chunk not found')
    graph_slice=_chunk_slice(plan,chunk);known={node['id'] for node in graph_slice['nodes']}
    if not known:raise ValueError('Review chunk has no graph nodes')
    request=build_prompt(graph_slice,plan['question']);request['chunk']={'id':chunk_id,'kind':chunk['kind'],'coverage_rule':'Review all supplied records. Return hypotheses and evidence gaps only.'}
    return plan,chunk,known,SYSTEM_PROMPT+'\n\nReview request:\n'+json.dumps(request,ensure_ascii=False)


def _vm_job_id(plan, chunk_id):
    return plan['plan_sha256'][:16]+'-'+chunk_id


def start_vm_review_chunk(plan_path, chunk_id, *, ssh_target, remote_root, dotenv_path, model='glm-5.3-flash', max_budget_usd=0.25):
    """Start a VM-local Claude job and return before the model finishes."""
    plan,chunk,known,prompt=_vm_review_request(plan_path,chunk_id)
    remote_root=remote_root.rstrip('/\\');job_id=_vm_job_id(plan,chunk_id)
    remote_prompt=remote_root+'/inbox/'+job_id+'.txt';remote_output=remote_root+'/out/'+job_id+'.json'
    with tempfile.TemporaryDirectory(prefix='foxpro-glm-') as temporary:
        local_prompt=Path(temporary)/'prompt.txt';local_prompt.write_text(prompt,encoding='utf-8')
        copied=subprocess.run(['scp',str(local_prompt),f'{ssh_target}:{remote_prompt}'],text=True,capture_output=True,timeout=120,check=False)
        if copied.returncode:raise RuntimeError('Unable to upload VM review shard: '+(copied.stderr or copied.stdout).strip()[-1000:])
    command=['ssh',ssh_target,'powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',remote_root+'/start-vm-glm-review.ps1','-RunnerPath',remote_root+'/run-review.ps1','-PromptFile',remote_prompt,'-OutputFile',remote_output,'-DotenvPath',dotenv_path,'-Model',model,'-MaxBudgetUsd',str(max_budget_usd)]
    started=subprocess.run(command,text=True,capture_output=True,timeout=60,check=False)
    if started.returncode:raise RuntimeError('Unable to start VM review shard: '+(started.stderr or started.stdout).strip()[-1000:])
    return {'job_id':job_id,'chunk_id':chunk_id,'state':'running','remote_output':remote_output,'executor':'credential-isolated-vm'}


def collect_vm_review_chunk(plan_path, chunk_id, *, ssh_target, remote_root, model='glm-5.3-flash'):
    """Collect a completed VM-local job without reading VM credentials."""
    plan,chunk,known,prompt=_vm_review_request(plan_path,chunk_id)
    remote_root=remote_root.rstrip('/\\');job_id=_vm_job_id(plan,chunk_id);remote_output=remote_root+'/out/'+job_id+'.json'
    available=subprocess.run(['ssh',ssh_target,'powershell.exe','-NoProfile','-Command',f"if (Test-Path -LiteralPath '{remote_output}') {{ 'ready' }} else {{ 'pending' }}"],text=True,capture_output=True,timeout=30,check=False)
    if available.returncode:raise RuntimeError('Unable to inspect VM review job: '+(available.stderr or available.stdout).strip()[-1000:])
    if available.stdout.strip()!='ready':return {'job_id':job_id,'chunk_id':chunk_id,'state':'running','executor':'credential-isolated-vm'}
    with tempfile.TemporaryDirectory(prefix='foxpro-glm-') as temporary:
        local_result=Path(temporary)/'result.json'
        retrieved=subprocess.run(['scp',f'{ssh_target}:{remote_output}',str(local_result)],text=True,capture_output=True,timeout=120,check=False)
        if retrieved.returncode:raise RuntimeError('Unable to retrieve VM review result: '+(retrieved.stderr or retrieved.stdout).strip()[-1000:])
        envelope=_read_claude_envelope(local_result)
    content=envelope.get('result') if isinstance(envelope,dict) else None
    response=parse_model_content(content)
    manifest=review_manifest(response,artifact_path=str(Path(plan_path).expanduser().resolve()),artifact_sha256=plan['plan_sha256'],tool_version=model,question=plan['question'],known_node_ids=known)
    manifest['details'].update({'review_chunk':chunk_id,'review_kind':chunk['kind'],'graph_project':plan['project'],'graph_fingerprint':plan['graph_fingerprint'],'executor':'credential-isolated-vm'})
    output=Path(plan_path).expanduser().resolve().parent/'manifests';output.mkdir(exist_ok=True)
    manifest_path=output/(chunk_id+'.json');manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return {'job_id':job_id,'chunk_id':chunk_id,'state':'complete','manifest_path':str(manifest_path),'candidate_count':len(response.get('candidates',[])),'gap_count':len(response.get('gaps',[])),'model':model,'executor':'credential-isolated-vm'}


def run_vm_review_chunk(plan_path, chunk_id, *, ssh_target, remote_root, dotenv_path, model='glm-5.3-flash', max_budget_usd=0.25, timeout_seconds=900):
    """Run one review shard in a remote VM; API credentials never leave that VM."""
    plan_path=Path(plan_path).expanduser().resolve();plan=json.loads(plan_path.read_text())
    chunk=next((item for item in plan['chunks'] if item['id']==chunk_id),None)
    if chunk is None:raise ValueError('Review chunk not found')
    graph_slice=_chunk_slice(plan,chunk);known={node['id'] for node in graph_slice['nodes']}
    if not known:raise ValueError('Review chunk has no graph nodes')
    request=build_prompt(graph_slice,plan['question']);request['chunk']={'id':chunk_id,'kind':chunk['kind'],'coverage_rule':'Review all supplied records. Return hypotheses and evidence gaps only.'}
    prompt=SYSTEM_PROMPT+'\n\nReview request:\n'+json.dumps(request,ensure_ascii=False)
    remote_root=remote_root.rstrip('/\\');token=uuid.uuid4().hex
    remote_prompt=remote_root+'/inbox/'+token+'.txt';remote_output=remote_root+'/out/'+token+'.json'
    with tempfile.TemporaryDirectory(prefix='foxpro-glm-') as temporary:
        local_prompt=Path(temporary)/'prompt.txt';local_result=Path(temporary)/'result.json'
        local_prompt.write_text(prompt,encoding='utf-8')
        copied=subprocess.run(['scp',str(local_prompt),f'{ssh_target}:{remote_prompt}'],text=True,capture_output=True,timeout=120,check=False)
        if copied.returncode:raise RuntimeError('Unable to upload VM review shard: '+(copied.stderr or copied.stdout).strip()[-1000:])
        command=['ssh',ssh_target,'powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',remote_root+'/run-review.ps1','-PromptFile',remote_prompt,'-OutputFile',remote_output,'-DotenvPath',dotenv_path,'-Model',model,'-MaxBudgetUsd',str(max_budget_usd)]
        completed=subprocess.run(command,text=True,capture_output=True,timeout=int(timeout_seconds),check=False)
        if completed.returncode:raise RuntimeError(f'VM Claude review failed for {chunk_id}: '+(completed.stderr or completed.stdout).strip()[-2000:])
        retrieved=subprocess.run(['scp',f'{ssh_target}:{remote_output}',str(local_result)],text=True,capture_output=True,timeout=120,check=False)
        if retrieved.returncode:raise RuntimeError('Unable to retrieve VM review result: '+(retrieved.stderr or retrieved.stdout).strip()[-1000:])
        envelope=_read_claude_envelope(local_result)
    content=envelope.get('result') if isinstance(envelope,dict) else None
    response=parse_model_content(content)
    manifest=review_manifest(response,artifact_path=str(plan_path),artifact_sha256=plan['plan_sha256'],tool_version=model,question=plan['question'],known_node_ids=known)
    manifest['details'].update({'review_chunk':chunk_id,'review_kind':chunk['kind'],'graph_project':plan['project'],'graph_fingerprint':plan['graph_fingerprint'],'executor':'credential-isolated-vm'})
    output=plan_path.parent/'manifests';output.mkdir(exist_ok=True)
    manifest_path=output/(chunk_id+'.json');manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return {'chunk_id':chunk_id,'manifest_path':str(manifest_path),'candidate_count':len(response.get('candidates',[])),'gap_count':len(response.get('gaps',[])),'model':model,'executor':'credential-isolated-vm'}


def run_vm_review_batch(plan_path, *, ssh_target, remote_root, dotenv_path, max_chunks=1, model='glm-5.3-flash', max_budget_usd=0.25, timeout_seconds=900):
    """Run pending review shards in a credential-isolated VM."""
    max_chunks=max(1,min(int(max_chunks),20));status=review_plan_status(plan_path)
    pending=[chunk['id'] for chunk in status['chunks'] if chunk['state']=='pending'][:max_chunks]
    results=[run_vm_review_chunk(plan_path,chunk_id,ssh_target=ssh_target,remote_root=remote_root,dotenv_path=dotenv_path,model=model,max_budget_usd=max_budget_usd,timeout_seconds=timeout_seconds) for chunk_id in pending]
    return {'results':results,'status':review_plan_status(plan_path),'executor':'credential-isolated-vm'}
