"""Read-only FoxPro source-container extraction. Never reads business DBF rows."""
from pathlib import Path
import hashlib
import struct

VERSION = 'foxpro-container-1'
CONTAINERS = {'.scx': '.sct', '.vcx': '.vct', '.mnx': '.mnt', '.frx': '.frt', '.dbc': '.dct'}
TEXT = {'.prg', '.mpr', '.h'}
CODE_FIELDS = {'.scx': {'METHODS'}, '.vcx': {'METHODS'}, '.mnx': {'COMMAND','PROCEDURE','SETUP','CLEANUP'}, '.frx': {'EXPR','SUPEXPR'}, '.dbc': {'CODE'}}
CODEPAGES = {0x01:'cp437', 0x02:'cp850', 0x03:'cp1252', 0x64:'cp852', 0x65:'cp866', 0x66:'cp865', 0xc8:'cp1250', 0xc9:'cp1251', 0xca:'cp1254', 0xcb:'cp1253', 0xcc:'cp1257'}

def digest(data):
    return hashlib.sha256(data).hexdigest()

def decode(data, codepage=None):
    warnings=[]
    if codepage is None:
        try:return data.decode('utf-8-sig'), 'utf-8', warnings
        except UnicodeDecodeError:
            encoding='cp1252'; warnings.append('No declared codepage; cp1252 assumed after UTF-8 failure')
    else:
        encoding=CODEPAGES.get(codepage,'cp1252')
        if codepage not in CODEPAGES: warnings.append(f'Unknown DBF codepage 0x{codepage:02x}; cp1252 assumed')
    try:return data.decode(encoding),encoding,warnings
    except UnicodeDecodeError:
        warnings.append(f'Invalid {encoding} byte(s) replaced; original byte hash retained')
        return data.decode(encoding,errors='replace'),encoding,warnings

def extract_file(path):
    """Return units, object metadata, issues, and original hashes for one source file."""
    path=Path(path); data=path.read_bytes(); ext=path.suffix.lower()
    result={'sha256':digest(data),'size':len(data),'units':[],'objects':[],'issues':[],'status':'indexed','extractor_version':VERSION}
    if ext in TEXT:
        text,encoding,warnings=decode(data)
        result['units'].append({'key':'text','source':text,'encoding':encoding,'warnings':warnings,'byte_start':0,'byte_end':len(data),'source_sha256':digest(data),'record':None,'field':None,'owner':''})
        return result
    if ext not in CONTAINERS:
        result['status']='opaque'; return result
    def issue(reason,**kw):result['issues'].append({'reason':reason,**kw});result['status']='partial'
    if len(data)<32:issue('Truncated DBF header');return result
    count=struct.unpack_from('<I',data,4)[0];header,size=struct.unpack_from('<HH',data,8);cp=data[29]
    result['codepage']=cp
    if header<33 or header>len(data) or size<1:issue('Invalid DBF header/record length');return result
    fields=[]; offset=1; terminated=False
    for pos in range(32,header,32):
        if data[pos]==13:terminated=True;break
        if pos+32>header:issue('Truncated DBF field descriptor');return result
        name=data[pos:pos+11].split(b'\0')[0].decode('ascii',errors='replace').upper();kind=chr(data[pos+11]);width=data[pos+16]
        if offset+width>size:issue('Field exceeds DBF record boundary',field=name);return result
        fields.append((name,kind,width,offset));offset+=width
    if not terminated:issue('Missing DBF field descriptor terminator');return result
    memo_path=path.with_suffix(CONTAINERS[ext])
    if not memo_path.exists():
        memo_path=next((p for p in path.parent.iterdir() if p.name.lower()==memo_path.name.lower()),memo_path)
    memo=b'';block_size=0
    if memo_path.exists():
        memo=memo_path.read_bytes();result['memo_path']=str(memo_path);result['memo_sha256']=digest(memo)
        if len(memo)>=8:block_size=struct.unpack_from('>H',memo,6)[0]
        if block_size<1:issue('Invalid memo block size')
    else:issue('Missing memo companion',memo_path=str(memo_path))
    if count>(len(data)-header)//size:issue('Declared records exceed file bounds')
    for record in range(min(count,(len(data)-header)//size)):
        start=header+record*size;row=data[start:start+size]
        if row[:1]==b'*':continue
        values={};locations={};warnings=[]
        for name,kind,width,offset in fields:
            raw=row[offset:offset+width]
            if kind=='M':
                if width==4:block=struct.unpack('<I',raw)[0]
                else:
                    try:block=int(raw.strip() or b'0')
                    except ValueError:issue('Invalid memo pointer',record=record+1,field=name);continue
                if block==0:values[name]='';continue
                pos=block*block_size
                if not block_size or pos<8 or pos+8>len(memo):issue('Memo pointer outside companion bounds',record=record+1,field=name,block=block);continue
                memo_type,length=struct.unpack_from('>II',memo,pos)
                if pos+8+length>len(memo):issue('Memo payload exceeds companion bounds',record=record+1,field=name,block=block);continue
                raw=memo[pos+8:pos+8+length]
                locations[name]={'memo_path':str(memo_path),'memo_block':block,'memo_type':memo_type,'byte_start':pos+8,'byte_end':pos+8+length,'source_sha256':digest(raw)}
                # Binary object code/OLE is inventoried, never decoded as source.
                if name in {'OBJCODE','OLE','OLE2'} or (ext=='.dbc' and name=='PROPERTY'):continue
            elif kind!='C':continue
            text,encoding,warn=decode(raw,cp);values[name]=text.rstrip('\0 ') if kind=='C' else text;warnings.extend(warn)
        uniqueid=values.get('UNIQUEID','');key=f'{record+1}:{uniqueid}'
        obj={'key':key,'record':record+1,'uniqueid':uniqueid,'name':values.get('OBJNAME',''),'parent':values.get('PARENT',''),'class':values.get('CLASS',''),'classloc':values.get('CLASSLOC',''),'baseclass':values.get('BASECLASS',''),'properties':values.get('PROPERTIES',''),'metadata':values,'locations':locations,'warnings':sorted(set(warnings))}
        result['objects'].append(obj)
        owner='.'.join(x for x in [obj['parent'],obj['name']] if x)
        for field in sorted(CODE_FIELDS[ext]):
            if ext=='.dbc' and values.get('OBJECTNAME','').casefold()!='storedproceduressource':continue
            if field not in values:continue
            raw_info=locations.get(field,{'byte_start':None,'byte_end':None,'source_sha256':digest(b'')})
            result['units'].append({'key':f'{key}:{field}','source':values[field],'encoding':CODEPAGES.get(cp,'cp1252'),'warnings':sorted(set(warnings)),'record':record+1,'uniqueid':uniqueid,'field':field,'owner':owner,'object_key':key,**raw_info})
    return result
