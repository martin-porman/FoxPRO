"""Persistent deterministic graph with explicit unresolved evidence and coverage."""
from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from .extract import extract_file, digest
from .parser import parse_source, lexical_cpg
from .evidence import load_manifests

SCHEMA_VERSION='2'
def js(value):return json.dumps(value,ensure_ascii=False,sort_keys=True)
def ident(*parts):return hashlib.sha256('\0'.join(str(x) for x in parts).encode()).hexdigest()[:24]

def build_index(source_root, db_path, project='joosep', extra_roots=None, evidence_paths=None):
    roots=[Path(source_root).resolve()]+[Path(x).resolve() for x in (extra_roots or [])]
    destination=Path(db_path).resolve()
    for root in roots:
        if destination.is_relative_to(root):raise ValueError('Index output must be outside read-only input roots')
        if not root.is_dir():raise ValueError(f'Source root is not a directory: {root}')
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix=destination.name+'.',suffix='.tmp',dir=destination.parent);os.close(fd)
    db=sqlite3.connect(temp)
    try:
        db.executescript('''
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE files(id TEXT PRIMARY KEY,root TEXT,path TEXT,absolute_path TEXT,sha256 TEXT,size INTEGER,status TEXT,details TEXT);
        CREATE TABLE artifacts(id TEXT PRIMARY KEY,file_id TEXT,kind TEXT,path TEXT,sha256 TEXT,details TEXT);
        CREATE TABLE evidence(id TEXT PRIMARY KEY,artifact_id TEXT,kind TEXT,tool TEXT,tool_version TEXT,location TEXT,observation TEXT,confidence TEXT);
        CREATE TABLE graph_nodes(id TEXT PRIMARY KEY,file_id TEXT,unit_id TEXT,kind TEXT,name TEXT,start_line INTEGER,end_line INTEGER,evidence_id TEXT,details TEXT);
        CREATE TABLE graph_edges(id TEXT PRIMARY KEY,source_id TEXT,target_id TEXT,kind TEXT,status TEXT,confidence TEXT,evidence_id TEXT,details TEXT);
        CREATE TABLE units(id TEXT PRIMARY KEY,file_id TEXT,unit_key TEXT,source TEXT,owner TEXT,provenance TEXT);
        CREATE TABLE objects(id TEXT PRIMARY KEY,file_id TEXT,object_key TEXT,name TEXT,parent TEXT,class TEXT,classloc TEXT,baseclass TEXT,details TEXT);
        CREATE TABLE symbols(id TEXT PRIMARY KEY,unit_id TEXT,file_id TEXT,name TEXT,canonical_name TEXT,kind TEXT,owner TEXT,start_line INTEGER,end_line INTEGER,details TEXT);
        CREATE TABLE refs(id TEXT PRIMARY KEY,unit_id TEXT,source_id TEXT,target TEXT,kind TEXT,line INTEGER,dynamic INTEGER,status TEXT,target_id TEXT,reason TEXT,candidates TEXT,details TEXT);
        CREATE TABLE edges(id TEXT PRIMARY KEY,source_id TEXT,target_id TEXT,kind TEXT,ref_id TEXT,provenance TEXT);
        CREATE TABLE issues(id TEXT PRIMARY KEY,file_id TEXT,unit_id TEXT,start_line INTEGER,end_line INTEGER,reason TEXT,details TEXT);
        CREATE INDEX symbol_names ON symbols(canonical_name); CREATE INDEX symbol_files ON symbols(file_id);
        CREATE INDEX edge_source ON edges(source_id); CREATE INDEX edge_target ON edges(target_id);
        CREATE INDEX ref_status ON refs(status);
        CREATE INDEX graph_node_kind ON graph_nodes(kind); CREATE INDEX graph_node_name ON graph_nodes(name);
        CREATE INDEX graph_edge_source ON graph_edges(source_id); CREATE INDEX graph_edge_target ON graph_edges(target_id);
        CREATE INDEX evidence_artifact ON evidence(artifact_id);
        ''')
        def add_node(node_id, *, file_id=None, unit_id=None, kind, name, start_line=None, end_line=None, evidence_id=None, details=None):
            db.execute('INSERT INTO graph_nodes VALUES(?,?,?,?,?,?,?,?,?)', (node_id, file_id, unit_id, kind, name, start_line, end_line, evidence_id, js(details or {})))

        def add_graph_edge(edge_id, source_id, target_id, kind, status, confidence, evidence_id=None, details=None):
            db.execute('INSERT INTO graph_edges VALUES(?,?,?,?,?,?,?,?)', (edge_id, source_id, target_id, kind, status, confidence, evidence_id, js(details or {})))

        external = load_manifests(evidence_paths)
        units={};files={};symbols=[];pending=[];objects=[];fingerprints=[];parser_versions=set();symbol_evidence={};file_nodes={};unit_nodes={}
        for root in roots:
            root_key=str(root)
            for path in sorted(root.rglob('*')):
                if not path.is_file() or path.is_symlink() or '.git' in path.relative_to(root).parts:continue
                rel=path.relative_to(root).as_posix();fid=ident(project,root_key,rel)
                try:result=extract_file(path)
                except (OSError,ValueError,OverflowError) as exc:result={'sha256':'','size':0,'status':'failed','units':[],'objects':[],'issues':[{'reason':str(exc)}]}
                files[fid]={'id':fid,'root':root_key,'path':rel,'absolute_path':str(path),'sha256':result['sha256'],'status':result['status']}
                detail={k:v for k,v in result.items() if k not in {'units','objects'}}
                db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?)',(fid,root_key,rel,str(path),result['sha256'],result['size'],result['status'],js(detail)))
                artifact_id=ident(fid,'artifact','source')
                db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',(artifact_id,fid,'SourceArtifact',str(path),result['sha256'],js({'root':root_key,'relative_path':rel,'size':result['size']})))
                file_evidence=ident(artifact_id,'evidence','read')
                db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',(file_evidence,artifact_id,'FileRead','native-extractor',result.get('extractor_version','unknown'),js({'path':str(path),'sha256':result['sha256']}),js({'status':result['status']}),'observed'))
                file_node=ident(fid,'node','SourceFile');file_nodes[fid]=file_node
                add_node(file_node,file_id=fid,kind='SourceFile',name=rel,evidence_id=file_evidence,details={'artifact_id':artifact_id,'status':result['status']})
                fingerprints.append((root_key,rel,result['sha256']))
                for n,issue in enumerate(result.get('issues',[])):
                    db.execute('INSERT INTO issues VALUES(?,?,?,?,?,?,?)',(ident(fid,'issue',n),fid,None,issue.get('start_line'),issue.get('end_line'),issue['reason'],js(issue)))
                for obj in result.get('objects',[]):
                    oid=ident(fid,'object',obj['key']);obj.update(id=oid,file_id=fid);objects.append(obj)
                    db.execute('INSERT INTO objects VALUES(?,?,?,?,?,?,?,?,?)',(oid,fid,obj['key'],obj['name'],obj['parent'],obj['class'],obj['classloc'],obj['baseclass'],js(obj)))
                    object_evidence=ident(artifact_id,'evidence','object',obj['key'])
                    db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',(object_evidence,artifact_id,'ContainerRecord','native-extractor',result.get('extractor_version','unknown'),js({'record':obj['record'],'key':obj['key'],'locations':obj.get('locations',{})}),js({'class':obj['class'],'baseclass':obj['baseclass']}),'observed'))
                    add_node(oid,file_id=fid,kind='VfpObject',name=obj['name'] or obj['key'],evidence_id=object_evidence,details={'record':obj['record'],'parent':obj['parent'],'class':obj['class'],'classloc':obj['classloc'],'baseclass':obj['baseclass']})
                    add_graph_edge(ident(oid,'artifact'),oid,file_node,'DEFINED_IN','resolved','observed',object_evidence,{'record':obj['record']})
                for unit in result['units']:
                    uid=ident(fid,unit['key']);units[uid]={**unit,'file_id':fid,'id':uid}
                    provenance={k:v for k,v in unit.items() if k!='source'};provenance.update(path=rel,absolute_path=str(path),root=root_key,original_sha256=result['sha256'],memo_sha256=result.get('memo_sha256'))
                    db.execute('INSERT INTO units VALUES(?,?,?,?,?,?)',(uid,fid,unit['key'],unit['source'],unit['owner'],js(provenance)))
                    unit_evidence=ident(artifact_id,'evidence','unit',unit['key'])
                    db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',(unit_evidence,artifact_id,'ExtractedSource','native-extractor',result.get('extractor_version','unknown'),js({k:v for k,v in provenance.items() if k != 'warnings'}),js({'source_sha256':unit.get('source_sha256'),'owner':unit['owner']}),'observed'))
                    unit_node=ident(uid,'node','SourceUnit');unit_nodes[uid]=unit_node
                    add_node(unit_node,file_id=fid,unit_id=uid,kind='SourceUnit',name=unit['key'],evidence_id=unit_evidence,details={'owner':unit['owner'],'field':unit.get('field'),'record':unit.get('record')})
                    add_graph_edge(ident(unit_node,'artifact'),unit_node,file_node,'EXTRACTED_FROM','resolved','observed',unit_evidence,{'unit_key':unit['key']})
                    for n,warning in enumerate(unit.get('warnings',[])):
                        db.execute('INSERT INTO issues VALUES(?,?,?,?,?,?,?)',(ident(uid,'encoding',n),fid,uid,1,max(1,len(unit['source'].splitlines())),warning,js({'encoding':unit['encoding']})))
                        db.execute("UPDATE files SET status='partial' WHERE id=?",(fid,));files[fid]['status']='partial'
                    parsed=parse_source(unit['source'],module=uid,owner=unit['owner']);cpg=lexical_cpg(unit['source']);parser_versions.update({parsed['parser_version'],cpg['version']});local={};statement_nodes={};variable_nodes={};data_nodes={}
                    for n,symbol in enumerate(parsed['symbols']):
                        sid=ident(uid,symbol['kind'],symbol['name'],symbol['start_line'],n)
                        s={**symbol,'id':sid,'unit_id':uid,'file_id':fid};symbols.append(s);local.setdefault(symbol['name'].casefold(),[]).append(s)
                        db.execute('INSERT INTO symbols VALUES(?,?,?,?,?,?,?,?,?,?)',(sid,uid,fid,symbol['name'],symbol['name'].casefold(),symbol['kind'],symbol.get('owner',''),symbol['start_line'],symbol['end_line'],js(symbol)))
                        symbol_evidence[sid]=unit_evidence
                        add_node(sid,file_id=fid,unit_id=uid,kind=symbol['kind'],name=symbol['name'],start_line=symbol['start_line'],end_line=symbol['end_line'],evidence_id=unit_evidence,details={'owner':symbol.get('owner','')})
                        add_graph_edge(ident(sid,'defined-in'),sid,unit_node,'DEFINED_IN','resolved','observed',unit_evidence,{'start_line':symbol['start_line'],'end_line':symbol['end_line']})

                    for statement in cpg['statements']:
                        node_id=ident(uid,'statement',statement['key']);statement_nodes[statement['key']]=node_id
                        statement_evidence=ident(unit_evidence,'statement',statement['key'])
                        # Source text is retained once in `units`.  Statement evidence keeps a
                        # hash and range so a large recovered system does not duplicate every line.
                        code_hash=digest(statement['code'].encode('utf-8'))
                        db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',(statement_evidence,artifact_id,'LexicalStatement','vfp-lexical-cpg',cpg['version'],js({'start_line':statement['start_line'],'end_line':statement['end_line']}),js({'code_sha256':code_hash,'kind':statement['kind']}),'observed'))
                        add_node(node_id,file_id=fid,unit_id=uid,kind='Statement:'+statement['kind'],name=statement['first_token'],start_line=statement['start_line'],end_line=statement['end_line'],evidence_id=statement_evidence,details={'code_sha256':code_hash})
                        add_graph_edge(ident(node_id,'unit'),node_id,unit_node,'PART_OF','resolved','observed',statement_evidence,{})
                    for flow in cpg['control_edges']:
                        source=statement_nodes[flow['source_key']];target=statement_nodes[flow['target_key']]
                        add_graph_edge(ident(uid,'cfg',flow['source_key'],flow['target_key'],flow['kind']),source,target,flow['kind'],'partial',flow['confidence'],None,{'layer':'control-flow','limitation':'lexical control flow; no preprocessor or runtime dispatch evaluation'})
                    for observation_number, observation in enumerate(cpg['variables']):
                        variable_key=(observation['scope'],observation['name'].casefold())
                        variable_id=variable_nodes.get(variable_key)
                        if variable_id is None:
                            variable_id=ident(uid,'variable',*variable_key);variable_nodes[variable_key]=variable_id
                            add_node(variable_id,file_id=fid,unit_id=uid,kind='Variable',name=observation['name'],evidence_id=unit_evidence,details={'scope':observation['scope']})
                        source=statement_nodes[observation['statement_key']]
                        add_graph_edge(ident(uid,'dfg',observation_number),source,variable_id,observation['kind'],'resolved' if observation['confidence']=='observed' else 'unresolved',observation['confidence'],None,{'layer':'data-flow','line':observation['line'],'scope':observation['scope']})
                    for observation_number, observation in enumerate(cpg['data_accesses']):
                        data_key=observation['name'].casefold();data_id=data_nodes.get(data_key)
                        if data_id is None:
                            data_id=ident(uid,'data-resource',data_key);data_nodes[data_key]=data_id
                            add_node(data_id,file_id=fid,unit_id=uid,kind='DataResource',name=observation['name'],evidence_id=unit_evidence,details={'identity':'lexical target; not a business-row read'})
                        source=statement_nodes[observation['statement_key']]
                        add_graph_edge(ident(uid,'data',observation_number),source,data_id,'DATA_'+observation['operation'],'resolved' if observation['confidence']=='observed' else 'unresolved',observation['confidence'],None,{'layer':'data-access','line':observation['line']})
                    for issue in cpg['issues']:
                        db.execute('INSERT INTO issues VALUES(?,?,?,?,?,?,?)',(ident(uid,'cpg-issue',issue['reason'],issue.get('start_line')),fid,uid,issue.get('start_line'),issue.get('start_line'),issue['reason'],js({'layer':'cpg'})))
                    for n,ref in enumerate(parsed['references']):
                        callers=local.get(ref.get('source_name','__program__').casefold(),[])
                        source=next((s for s in callers if s['start_line']<=ref['source_line']<=s['end_line']),callers[0] if callers else None)
                        pending.append({**ref,'id':ident(uid,'ref',n),'unit_id':uid,'file_id':fid,'source_id':source['id'] if source else None})
                    for n,issue in enumerate(parsed['issues']):
                        db.execute('INSERT INTO issues VALUES(?,?,?,?,?,?,?)',(ident(uid,'issue',n),fid,uid,issue.get('start_line'),issue.get('end_line'),issue['reason'],js(issue)))
                        db.execute("UPDATE files SET status='partial' WHERE id=?",(fid,));files[fid]['status']='partial'
        # Index keys retain deployment provenance: resolution never crosses roots.
        byfile={};byname={};programs={}
        for s in symbols:
            byfile.setdefault(s['file_id'],[]).append(s)
            byname.setdefault(s['name'].casefold(),[]).append(s)
            if s['kind']=='Program':programs.setdefault(s['file_id'],[]).append(s)
        def path_candidates(ref,target,extensions):
            origin=files[ref['file_id']]; normalized=target.strip(' \"\'').replace('\\','/').casefold()
            if not normalized:return []
            wanted={normalized} if Path(normalized).suffix else {normalized+ext for ext in extensions}
            parent=Path(origin['path']).parent.as_posix();relative={str(Path(parent)/w).casefold() for w in wanted}
            exact=[f for f in files.values() if f['root']==origin['root'] and f['path'].casefold() in relative]
            if not exact:exact=[f for f in files.values() if f['root']==origin['root'] and f['path'].casefold() in wanted]
            # basename fallback is restricted to a unique file within this root.
            if not exact and '/' not in normalized:exact=[f for f in files.values() if f['root']==origin['root'] and Path(f['path']).name.casefold() in wanted]
            return exact
        for ref in pending:
            candidates=[];reason=ref.get('reason','');target=ref['target'];kind=ref['kind']
            if ref.get('dynamic'):reason=reason or 'Dynamic target requires runtime evidence'
            elif 'builtin' in reason.lower():pass
            elif kind in {'IMPORTS','OPENS_FORM'}:
                matched=path_candidates(ref,target,['.scx'] if kind=='OPENS_FORM' else ['.prg','.h','.vcx'])
                for f in matched:candidates.append({'id':f['id'],'kind':'File'})
                reason='Static file reference' if candidates else 'Static target file unavailable'
            elif kind in {'CALLS','INHERITS','INSTANTIATES'}:
                name=target.casefold();pool=byname.get(name,[])
                if kind=='CALLS':pool=[s for s in pool if s['kind']=='Function']
                else:pool=[s for s in pool if s['kind']=='Class']
                # No global unique-name resolution: lexical unit, then file, then explicit library.
                candidates=[s for s in pool if s['unit_id']==ref['unit_id']]
                if not candidates:candidates=[s for s in pool if s['file_id']==ref['file_id'] and not s.get('owner')]
                library=ref.get('library')
                if not candidates and library:
                    fids={f['id'] for f in path_candidates(ref,library,['.prg','.vcx'])};candidates=[s for s in pool if s['file_id'] in fids]
                if not candidates and kind=='CALLS':
                    matched=path_candidates(ref,target,['.prg','.mpr'])
                    candidates=[programs[f['id']][0] for f in matched if programs.get(f['id'])]
                reason='Lexical or explicit file/library resolution' if candidates else (reason or 'No proven lexical or explicit-library target')
            else:reason=reason or 'Data reference retained; business data is not read'
            # Keep ambiguity visible, never choose the first candidate.
            unique={s['id']:s for s in candidates};candidates=list(unique.values());resolved=len(candidates)==1 and not ref.get('dynamic')
            status='resolved' if resolved else ('ambiguous' if len(candidates)>1 else 'unresolved');tid=candidates[0]['id'] if resolved else None
            if len(candidates)>1:reason='Multiple candidates; no target selected'
            db.execute('INSERT INTO refs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(ref['id'],ref['unit_id'],ref['source_id'],target,kind,ref['source_line'],int(bool(ref.get('dynamic'))),status,tid,reason,js([s['id'] for s in candidates]),js(ref)))
            source_node=ref['source_id'] or unit_nodes[ref['unit_id']]
            ref_evidence=ident(ref['id'],'evidence')
            source_artifact=ident(ref['file_id'],'artifact','source')
            db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',(ref_evidence,source_artifact,'LexicalReference','vfp-lexical-cpg','2.0.0',js({'line':ref['source_line'],'unit_id':ref['unit_id']}),js({'target_text':target,'reason':reason,'dynamic':bool(ref.get('dynamic'))}),'observed' if not ref.get('dynamic') else 'unresolved'))
            if resolved:
                target_node=file_nodes.get(tid,tid)
            else:
                target_node=ident(ref['id'],'unresolved-target')
                add_node(target_node,file_id=ref['file_id'],unit_id=ref['unit_id'],kind='UnresolvedReference',name=target,evidence_id=ref_evidence,details={'reason':reason,'candidate_ids':[s['id'] for s in candidates],'dynamic':bool(ref.get('dynamic'))})
            add_graph_edge(ident(ref['id'],'evidence-edge'),source_node,target_node,kind,status,'observed' if resolved else 'unresolved',ref_evidence,{'source_line':ref['source_line'],'reason':reason,'candidate_ids':[s['id'] for s in candidates]})
            if resolved and ref['source_id']:
                db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?)',(ident(ref['id'],'edge'),ref['source_id'],tid,kind,ref['id'],js({'resolution':reason,'source_line':ref['source_line'],'unit_id':ref['unit_id']})))
        # Object containment is metadata evidence, separate from executable CALLS.
        object_paths={}
        for obj in objects:
            object_paths.setdefault((obj['file_id'],'.'.join(x for x in [obj['parent'],obj['name']] if x).casefold()),[]).append(obj)
        for obj in objects:
            parent=object_paths.get((obj['file_id'],obj['parent'].casefold()),[]) if obj['parent'] else []
            if len(parent)==1:
                db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?)',(ident(obj['id'],'parent'),parent[0]['id'],obj['id'],'CONTAINS',None,js({'source':'container metadata','record':obj['record']})))
                add_graph_edge(ident(obj['id'],'parent-cpg'),parent[0]['id'],obj['id'],'CONTAINS','resolved','observed',None,{'source':'container metadata','record':obj['record']})
            if obj['classloc'] and obj['class']:
                linked_files=path_candidates({'file_id':obj['file_id']},obj['classloc'],['.vcx'])
                matches=[candidate for f in linked_files for candidate in object_paths.get((f['id'],obj['class'].casefold()),[])]
                if len(matches)==1:
                    db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?)',(ident(obj['id'],'class'),obj['id'],matches[0]['id'],'INHERITS',None,js({'source':'CLASS/CLASSLOC container metadata','record':obj['record']})))
                    add_graph_edge(ident(obj['id'],'class-cpg'),obj['id'],matches[0]['id'],'INHERITS','resolved','observed',None,{'source':'CLASS/CLASSLOC container metadata','record':obj['record']})
        # External tools contribute observations, never rewritten source.  They
        # link only through the manifest's artifact hash and explicit links.
        external_nodes={}
        for manifest in external['manifests']:
            matching_file=next((f for f in files.values() if f['sha256']==manifest['artifact_sha256'] and (f['absolute_path']==manifest['artifact_path'] or f['path']==manifest['artifact_path'])),None)
            external_artifact=ident(project,'external-artifact',manifest['sha256'])
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',(external_artifact,matching_file['id'] if matching_file else None,'ExternalArtifact',manifest['artifact_path'],manifest['artifact_sha256'],js({'adapter':manifest['adapter'],'manifest_path':manifest['path'],'manifest_sha256':manifest['sha256']})))
            manifest_evidence=ident(external_artifact,'manifest')
            db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',(manifest_evidence,external_artifact,'ExternalManifest',manifest['tool_name'],manifest['tool_version'],js({'manifest_path':manifest['path'],'manifest_sha256':manifest['sha256']}),js(manifest['details']),'observed'))
            artifact_node=ident(external_artifact,'node')
            add_node(artifact_node,file_id=matching_file['id'] if matching_file else None,kind='ExternalArtifact',name=manifest['artifact_path'],evidence_id=manifest_evidence,details={'adapter':manifest['adapter'],'sha256':manifest['artifact_sha256']})
            if matching_file:
                add_graph_edge(ident(external_artifact,'same-artifact'),artifact_node,file_nodes[matching_file['id']],'SAME_ARTIFACT','resolved','hash-match',manifest_evidence,{'sha256':manifest['artifact_sha256']})
            for observation in (x for x in external['observations'] if x['manifest_sha256']==manifest['sha256']):
                observation_evidence=ident(manifest_evidence,'observation',observation['key'])
                db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',(observation_evidence,external_artifact,observation['kind'],manifest['tool_name'],manifest['tool_version'],js(observation['location']),js(observation['details']),observation['confidence']))
                node_id=ident(external_artifact,'observation',observation['key']);external_nodes[(manifest['sha256'],observation['key'])]=node_id
                add_node(node_id,file_id=matching_file['id'] if matching_file else None,kind='External:'+observation['kind'],name=observation['name'],evidence_id=observation_evidence,details={'adapter':manifest['adapter'],'location':observation['location'],'details':observation['details']})
                add_graph_edge(ident(node_id,'observed-in'),node_id,artifact_node,'OBSERVED_IN','resolved',observation['confidence'],observation_evidence,{})
        for link in external['links']:
            source=external_nodes[(link['manifest_sha256'],link['source'])];target=external_nodes[(link['manifest_sha256'],link['target'])]
            add_graph_edge(ident(link['manifest_sha256'],'link',link['source'],link['target'],link['kind']),source,target,link['kind'],'observed',link['confidence'],None,link['details'])
        fingerprint=ident(SCHEMA_VERSION,sorted(parser_versions),js(fingerprints))
        metadata={'schema_version':SCHEMA_VERSION,'project':project,'roots':[str(r) for r in roots],'evidence_paths':[m['path'] for m in external['manifests']],'generation':datetime.now(timezone.utc).isoformat(),'graph_fingerprint':fingerprint,'parser_versions':sorted(parser_versions),'reference_coverage':'non-exhaustive static extraction plus explicit external-tool evidence; unresolved dynamic behavior is retained and no layer is a proof of runtime behavior without a trace.'}
        db.executemany('INSERT INTO metadata VALUES(?,?)',[(k,js(v)) for k,v in metadata.items()]);db.commit();db.close();os.replace(temp,destination)
    except BaseException:
        db.close();Path(temp).unlink(missing_ok=True);raise
    return Graph(destination).index_status()

class Graph:
    def __init__(self,db_path):self.db_path=str(db_path)
    def _query(self,sql,args=()):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory=sqlite3.Row;return [dict(row) for row in db.execute(sql,args)]
    def index_status(self):
        metadata={r['key']:json.loads(r['value']) for r in self._query('SELECT * FROM metadata')}
        metadata['counts']={t:self._query(f'SELECT count(*) AS n FROM {t}')[0]['n'] for t in ['files','artifacts','evidence','graph_nodes','graph_edges','units','objects','symbols','refs','edges','issues']}
        metadata['symbols_by_kind']=self._query('SELECT kind,count(*) AS count FROM symbols GROUP BY kind')
        metadata['files_by_status']=self._query('SELECT status,count(*) AS count FROM files GROUP BY status')
        metadata['references_by_status']=self._query('SELECT status,count(*) AS count FROM refs GROUP BY status')
        return metadata

    def get_architecture(self):
        status=self.index_status()
        status['graph_nodes_by_kind']=self._query('SELECT kind,count(*) AS count FROM graph_nodes GROUP BY kind ORDER BY kind')
        status['graph_edges_by_kind']=self._query('SELECT kind,status,count(*) AS count FROM graph_edges GROUP BY kind,status ORDER BY kind,status')
        status['evidence_by_tool']=self._query('SELECT tool,tool_version,kind,count(*) AS count FROM evidence GROUP BY tool,tool_version,kind ORDER BY tool,kind')
        status['architecture_limitations']='Lexical syntax/control/data observations and imported evidence are distinct layers. Only trace-backed external evidence may establish observed runtime behavior.'
        return status

    def query_graph(self, kind=None, edge_kind=None, name_pattern=None, evidence_kind=None, status=None, limit=100, offset=0):
        limit=max(1,min(int(limit),1000));offset=max(0,int(offset))
        nodes=self._query('SELECT * FROM graph_nodes ORDER BY kind,name,id')
        if kind:nodes=[node for node in nodes if node['kind'].casefold()==kind.casefold()]
        if name_pattern:
            if len(name_pattern)>200:raise ValueError('Pattern too long')
            pattern=re.compile(name_pattern,re.I);nodes=[node for node in nodes if pattern.search(node['name'])]
        if evidence_kind:
            matching={row['id'] for row in self._query('SELECT id FROM evidence WHERE kind=?',(evidence_kind,))};nodes=[node for node in nodes if node['evidence_id'] in matching]
        node_ids={node['id'] for node in nodes}
        edges=self._query('SELECT * FROM graph_edges ORDER BY kind,id')
        if edge_kind:edges=[edge for edge in edges if edge['kind'].casefold()==edge_kind.casefold()]
        if status:edges=[edge for edge in edges if edge['status'].casefold()==status.casefold()]
        if node_ids:edges=[edge for edge in edges if edge['source_id'] in node_ids or edge['target_id'] in node_ids]
        elif kind or name_pattern or evidence_kind:edges=[]
        return {'nodes':nodes[offset:offset+limit],'edges':edges[:limit],'total_nodes':len(nodes),'total_edges':len(edges),'offset':offset,'limit':limit,'has_more':offset+limit<len(nodes),'limitations':'Graph query exposes explicit evidence layers; it does not infer missing runtime relationships.'}

    def get_evidence(self,node_id):
        nodes=self._query('SELECT * FROM graph_nodes WHERE id=?',(node_id,))
        if not nodes:return {'error':'Graph node not found'}
        node=nodes[0]
        evidence=self._query('SELECT * FROM evidence WHERE id=?',(node['evidence_id'],)) if node['evidence_id'] else []
        for row in evidence:
            row['location']=json.loads(row['location']);row['observation']=json.loads(row['observation'])
        return {'node':node,'evidence':evidence,'incoming':self._query('SELECT * FROM graph_edges WHERE target_id=?',(node_id,)),'outgoing':self._query('SELECT * FROM graph_edges WHERE source_id=?',(node_id,))}

    def trace_graph(self,node_id,direction='both',depth=2):
        if direction not in {'outbound','inbound','both'}:raise ValueError('Invalid direction')
        depth=max(0,min(int(depth),8));seen={node_id};front={node_id};edges={}
        for _ in range(depth):
            nxt=set()
            for current in front:
                clauses=[];args=[]
                if direction in {'outbound','both'}:clauses.append('source_id=?');args.append(current)
                if direction in {'inbound','both'}:clauses.append('target_id=?');args.append(current)
                for edge in self._query('SELECT * FROM graph_edges WHERE '+' OR '.join(clauses),args):
                    edges[edge['id']]=edge;nxt.update((edge['source_id'],edge['target_id']))
            front=nxt-seen;seen.update(nxt)
            if not front or len(edges)>5000:break
        nodes=[]
        for current in sorted(seen):
            rows=self._query('SELECT * FROM graph_nodes WHERE id=?',(current,))
            if rows:nodes.append(rows[0])
        return {'nodes':nodes,'edges':list(edges.values()),'depth':depth,'limitations':'Includes observed, partial and unresolved relations. Inspect each edge status and evidence before treating it as behavior.'}
    def search_graph(self,query=None,name_pattern=None,label=None,path=None,limit=100,offset=0):
        limit=max(1,min(int(limit),1000));offset=max(0,int(offset));rows=self._query('SELECT s.*,f.path,f.root FROM symbols s JOIN files f ON f.id=s.file_id ORDER BY f.root,f.path,s.start_line,s.id')
        if query:rows=[r for r in rows if query.casefold() in r['name'].casefold()]
        if name_pattern:
            if len(name_pattern)>200:raise ValueError('Pattern too long')
            pattern=re.compile(name_pattern,re.I);rows=[r for r in rows if pattern.search(r['name'])]
        if label:rows=[r for r in rows if r['kind'].casefold()==label.casefold()]
        if path:rows=[r for r in rows if path.casefold() in r['path'].casefold()]
        return {'results':rows[offset:offset+limit],'total':len(rows),'offset':offset,'limit':limit,'has_more':offset+limit<len(rows)}
    def get_code_snippet(self,symbol_id):
        rows=self._query('SELECT s.*,u.source,u.provenance,f.path FROM symbols s JOIN units u ON u.id=s.unit_id JOIN files f ON f.id=s.file_id WHERE s.id=?',(symbol_id,))
        if not rows:return {'error':'Symbol not found'}
        row=rows[0];lines=row.pop('source').splitlines();row['code']='\n'.join(lines[max(0,row['start_line']-1):row['end_line']]);row['provenance']=json.loads(row['provenance']);return row
    def trace_path(self,symbol_id,direction='outbound',depth=2):
        if direction not in {'outbound','inbound','both'}:raise ValueError('Invalid direction')
        depth=max(0,min(int(depth),8));seen={symbol_id};front={symbol_id};edges={}
        for _ in range(depth):
            nxt=set()
            for sid in front:
                clauses=[];args=[]
                if direction in {'outbound','both'}:clauses.append('source_id=?');args.append(sid)
                if direction in {'inbound','both'}:clauses.append('target_id=?');args.append(sid)
                for edge in self._query('SELECT * FROM edges WHERE '+' OR '.join(clauses),args):
                    edges[edge['id']]=edge;nxt.update([edge['source_id'],edge['target_id']])
            front=nxt-seen;seen.update(nxt)
            if not front or len(edges)>5000:break
        return {'edges':list(edges.values()),'node_ids':sorted(seen),'unresolved':self._query("SELECT * FROM refs WHERE source_id=? AND status!='resolved'",(symbol_id,)),'reference_coverage':'non-exhaustive static extraction'}
    def check_index_coverage(self,paths=None,scopes=None):
        all_rows=self._query('SELECT * FROM files ORDER BY root,path')
        requested_paths=list(paths or [])
        requested_scopes=list(scopes or [])
        def in_scope(row, scope):
            normalized=scope.replace('\\\\','/').rstrip('/')
            return normalized in {'', '.'} or row['path']==normalized or row['path'].startswith(normalized+'/')
        rows=all_rows
        if requested_paths or requested_scopes:
            rows=[r for r in all_rows if r['path'] in requested_paths or r['absolute_path'] in requested_paths or any(in_scope(r, scope) for scope in requested_scopes)]
        found={r['path'] for r in all_rows}|{r['absolute_path'] for r in all_rows}
        missing=[p for p in requested_paths if p not in found]
        missing_scopes=[scope for scope in requested_scopes if not any(in_scope(row, scope) for row in all_rows)]
        fids={r['id'] for r in rows};issues=[i for i in self._query('SELECT * FROM issues') if i['file_id'] in fids]
        changed=[]
        for row in rows:
            try:
                current=digest(Path(row['absolute_path']).read_bytes())
            except OSError:
                changed.append({'path':row['path'],'reason':'Source file is no longer readable'})
            else:
                if current != row['sha256']:
                    changed.append({'path':row['path'],'reason':'Source changed since this index generation'})
        return {'files':rows,'missing_paths':missing,'missing_scopes':missing_scopes,'changed_files':changed,'issues':issues,'complete':not missing and not missing_scopes and not changed and all(r['status']=='indexed' for r in rows) and not issues,'limitations':'No recorded gap is not proof of completeness. References are non-exhaustive; runtime dispatch is unresolved. Opaque resources are inventoried, not parsed.'}
