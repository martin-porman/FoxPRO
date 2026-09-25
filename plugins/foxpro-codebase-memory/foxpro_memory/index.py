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
from .parser import parse_source

SCHEMA_VERSION='1'
def js(value):return json.dumps(value,ensure_ascii=False,sort_keys=True)
def ident(*parts):return hashlib.sha256('\0'.join(str(x) for x in parts).encode()).hexdigest()[:24]

def build_index(source_root, db_path, project='joosep', extra_roots=None):
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
        CREATE TABLE units(id TEXT PRIMARY KEY,file_id TEXT,unit_key TEXT,source TEXT,owner TEXT,provenance TEXT);
        CREATE TABLE objects(id TEXT PRIMARY KEY,file_id TEXT,object_key TEXT,name TEXT,parent TEXT,class TEXT,classloc TEXT,baseclass TEXT,details TEXT);
        CREATE TABLE symbols(id TEXT PRIMARY KEY,unit_id TEXT,file_id TEXT,name TEXT,canonical_name TEXT,kind TEXT,owner TEXT,start_line INTEGER,end_line INTEGER,details TEXT);
        CREATE TABLE refs(id TEXT PRIMARY KEY,unit_id TEXT,source_id TEXT,target TEXT,kind TEXT,line INTEGER,dynamic INTEGER,status TEXT,target_id TEXT,reason TEXT,candidates TEXT,details TEXT);
        CREATE TABLE edges(id TEXT PRIMARY KEY,source_id TEXT,target_id TEXT,kind TEXT,ref_id TEXT,provenance TEXT);
        CREATE TABLE issues(id TEXT PRIMARY KEY,file_id TEXT,unit_id TEXT,start_line INTEGER,end_line INTEGER,reason TEXT,details TEXT);
        CREATE INDEX symbol_names ON symbols(canonical_name); CREATE INDEX symbol_files ON symbols(file_id);
        CREATE INDEX edge_source ON edges(source_id); CREATE INDEX edge_target ON edges(target_id);
        CREATE INDEX ref_status ON refs(status);
        ''')
        units={};files={};symbols=[];pending=[];objects=[];fingerprints=[];parser_versions=set()
        for root in roots:
            root_key=str(root)
            for path in sorted(root.rglob('*')):
                if not path.is_file() or path.is_symlink() or '.git' in path.relative_to(root).parts:continue
                rel=path.relative_to(root).as_posix();fid=ident(project,root_key,rel)
                try:result=extract_file(path)
                except (OSError,ValueError,OverflowError) as exc:result={'sha256':'','size':0,'status':'failed','units':[],'objects':[],'issues':[{'reason':str(exc)}]}
                files[fid]={'id':fid,'root':root_key,'path':rel,'absolute_path':str(path),'status':result['status']}
                detail={k:v for k,v in result.items() if k not in {'units','objects'}}
                db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?)',(fid,root_key,rel,str(path),result['sha256'],result['size'],result['status'],js(detail)))
                fingerprints.append((root_key,rel,result['sha256']))
                for n,issue in enumerate(result.get('issues',[])):
                    db.execute('INSERT INTO issues VALUES(?,?,?,?,?,?,?)',(ident(fid,'issue',n),fid,None,issue.get('start_line'),issue.get('end_line'),issue['reason'],js(issue)))
                for obj in result.get('objects',[]):
                    oid=ident(fid,'object',obj['key']);obj.update(id=oid,file_id=fid);objects.append(obj)
                    db.execute('INSERT INTO objects VALUES(?,?,?,?,?,?,?,?,?)',(oid,fid,obj['key'],obj['name'],obj['parent'],obj['class'],obj['classloc'],obj['baseclass'],js(obj)))
                for unit in result['units']:
                    uid=ident(fid,unit['key']);units[uid]={**unit,'file_id':fid,'id':uid}
                    provenance={k:v for k,v in unit.items() if k!='source'};provenance.update(path=rel,absolute_path=str(path),root=root_key,original_sha256=result['sha256'],memo_sha256=result.get('memo_sha256'))
                    db.execute('INSERT INTO units VALUES(?,?,?,?,?,?)',(uid,fid,unit['key'],unit['source'],unit['owner'],js(provenance)))
                    for n,warning in enumerate(unit.get('warnings',[])):
                        db.execute('INSERT INTO issues VALUES(?,?,?,?,?,?,?)',(ident(uid,'encoding',n),fid,uid,1,max(1,len(unit['source'].splitlines())),warning,js({'encoding':unit['encoding']})))
                        db.execute("UPDATE files SET status='partial' WHERE id=?",(fid,));files[fid]['status']='partial'
                    parsed=parse_source(unit['source'],module=uid,owner=unit['owner']);parser_versions.add(parsed['parser_version']);local={}
                    for n,symbol in enumerate(parsed['symbols']):
                        sid=ident(uid,symbol['kind'],symbol['name'],symbol['start_line'],n)
                        s={**symbol,'id':sid,'unit_id':uid,'file_id':fid};symbols.append(s);local.setdefault(symbol['name'].casefold(),[]).append(s)
                        db.execute('INSERT INTO symbols VALUES(?,?,?,?,?,?,?,?,?,?)',(sid,uid,fid,symbol['name'],symbol['name'].casefold(),symbol['kind'],symbol.get('owner',''),symbol['start_line'],symbol['end_line'],js(symbol)))
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
            if resolved and ref['source_id']:
                db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?)',(ident(ref['id'],'edge'),ref['source_id'],tid,kind,ref['id'],js({'resolution':reason,'source_line':ref['source_line'],'unit_id':ref['unit_id']})))
        # Object containment is metadata evidence, separate from executable CALLS.
        object_paths={}
        for obj in objects:
            object_paths.setdefault((obj['file_id'],'.'.join(x for x in [obj['parent'],obj['name']] if x).casefold()),[]).append(obj)
        for obj in objects:
            parent=object_paths.get((obj['file_id'],obj['parent'].casefold()),[]) if obj['parent'] else []
            if len(parent)==1:db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?)',(ident(obj['id'],'parent'),parent[0]['id'],obj['id'],'CONTAINS',None,js({'source':'container metadata','record':obj['record']})))
            if obj['classloc'] and obj['class']:
                linked_files=path_candidates({'file_id':obj['file_id']},obj['classloc'],['.vcx'])
                matches=[candidate for f in linked_files for candidate in object_paths.get((f['id'],obj['class'].casefold()),[])]
                if len(matches)==1:
                    db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?)',(ident(obj['id'],'class'),obj['id'],matches[0]['id'],'INHERITS',None,js({'source':'CLASS/CLASSLOC container metadata','record':obj['record']})))
        fingerprint=ident(SCHEMA_VERSION,sorted(parser_versions),js(fingerprints))
        metadata={'schema_version':SCHEMA_VERSION,'project':project,'roots':[str(r) for r in roots],'generation':datetime.now(timezone.utc).isoformat(),'graph_fingerprint':fingerprint,'parser_versions':sorted(parser_versions),'reference_coverage':'non-exhaustive static extraction; unresolved dynamic behavior retained; not a proof of runtime behavior'}
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
        metadata['counts']={t:self._query(f'SELECT count(*) AS n FROM {t}')[0]['n'] for t in ['files','units','objects','symbols','refs','edges','issues']}
        metadata['symbols_by_kind']=self._query('SELECT kind,count(*) AS count FROM symbols GROUP BY kind')
        metadata['files_by_status']=self._query('SELECT status,count(*) AS count FROM files GROUP BY status')
        metadata['references_by_status']=self._query('SELECT status,count(*) AS count FROM refs GROUP BY status')
        return metadata
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
