"""Conservative structural extraction for decompiled Visual FoxPro.

This is a lexical indexer, not a FoxPro compiler. References describe syntax;
resolution, confidence and cross-file identity belong to the graph builder.
"""
import re

PARSER_VERSION = 'vfp-lexical-1.0.0'
# These cannot safely be resolved by matching a user routine's short name.
BUILTINS = frozenset('ABS ACOPY ADATABASES ADBOBJECTS ADDBS ADEL AERROR AFIELDS AGETCLASS AINS ALEN ALLTRIM AMEMBERS ASC ASCAN ASORT AT ATC ATLINE ATCLINE BETWEEN BOF CDOW CEILING CHR CHRTRAN CMONTH CNTBAR COL COMPOBJ COS CREATEOBJECT CTOD CTOT CURDIR CURSORGETPROP CURSORSETPROP DATE DATETIME DAY DBF DBGETPROP DBSETPROP DELETED DIRECTORY DMY DODEFAULT DTOC DTOR DTOS DTOT EMPTY EOF ERROR EVALUATE EVL EXP FCOUNT FCREATE FCLOSE FEOF FGETS FIELD FILE FILETOSTR FLUSH FLOOR FOPEN FPUTS FREAD FSEEK FULLPATH FV FWRITE GETENV GETFILE GETPEM GETWORDCOUNT GETWORDNUM GOMONTH HOUR ICASE IIF INDBC INKEY INLIST INT ISALPHA ISBLANK ISDIGIT ISEXCLUSIVE ISFLOCKED ISLEADBYTE ISLOWER ISMEMOFETCH ISNULL ISPEN ISREADONLY ISRLOCKED ISUPPER JUSTDRIVE JUSTEXT JUSTFNAME JUSTPATH JUSTSTEM LEFT LEN LIKE LINENO LOCK LOG LOG10 LOWER LTRIM MAX MDX MESSAGE MIN MINUTE MOD MONTH MROW NCOLS NEWOBJECT NOFILTER NVL OCCURS OS PADL PADR PADC PARAMETERS PCOUNT PEMSTATUS PI PROGRAM RAT RATLINE RECCOUNT RECNO RECSIZE REPLICATE RGB RIGHT RLOCK ROUND RTRIM SECOND SECONDS SEEK SELECT SET SIGN SIN SKPBAR SPACE SQLCANCEL SQLCOLUMNS SQLCOMMIT SQLCONNECT SQLDISCONNECT SQLEXEC SQLGETPROP SQLIDLEDISCONNECT SQLMORERESULTS SQLPREPARE SQLROLLBACK SQLSETPROP SQLSTRINGCONNECT SQLTABLES SQRT STOD STR STRCONV STRTOFILE STRTRAN STUFF SUBSTR SYS TAN TIME TRANSFORM TRIM TTOC TTOD TYPE UPPER USED VAL VARTYPE WEEK WEXIST YEAR'.split())
_IDENT = re.compile(r'[A-Za-z_\u0080-\uffff][\w\u0080-\uffff]*(?:\.[A-Za-z_\u0080-\uffff][\w\u0080-\uffff]*)*')


def _tokens(line):
    """Return (kind, value) tokens. Comments and string bodies never become code."""
    out = []; i = 0
    if re.match(r'^\s*(?:\*|NOTE(?:\s|$))', line, re.I):
        return out
    while i < len(line):
        c = line[i]
        if c.isspace(): i += 1; continue
        if line.startswith('&&', i): break
        if c in "\"'" or (c == '[' and not (i > 0 and not line[i-1].isspace() and out and (out[-1][0] == 'id' or out[-1][1] in (')', ']')))):
            end = ']' if c == '[' else c; j = i + 1; value = ''
            while j < len(line):
                if line[j] == end:
                    if end != ']' and j + 1 < len(line) and line[j+1] == end:
                        value += end; j += 2; continue
                    break
                value += line[j]; j += 1
            out.append(('str' if j < len(line) else 'unterminated_str', value)); i = min(j + 1, len(line)); continue
        match = _IDENT.match(line, i)
        if match:
            out.append(('id', match.group())); i = match.end(); continue
        out.append(('punct', c)); i += 1
    return out


def parse_source(text: str, *, module: str = '', owner: str = '') -> dict:
    lines = text.splitlines(); total = max(1, len(lines))
    symbols = [{'name':'__program__','kind':'Program','start_line':1,'end_line':total,'owner':owner}]
    refs = []; issues = []; routine = None; cls = None; arrays = {}; text_start = None; with_depth = 0
    conditional_depth = 0
    def issue(start, end, reason): issues.append({'start_line':start,'end_line':end,'reason':reason})
    def close_routine(end):
        nonlocal routine
        if routine is not None: routine['end_line'] = max(routine['start_line'], end); routine = None
    def context(): return routine['name'] if routine else (cls['name'] if cls else '__program__')
    def ref(target, kind, lineno, dynamic=False, reason='', library=''):
        if not target: return
        item={'source_name':context(),'source_line':lineno,'target':target,'kind':kind,'dynamic':dynamic}
        if reason: item['reason']=reason
        if library: item['library']=library
        refs.append(item)
    logical=[]; pending=[]; pending_start=1
    for lineno, line in enumerate(lines, 1):
        if text_start is not None:
            if re.match(r'^\s*ENDTEXT\b', line, re.I): text_start=None
            continue
        ts=_tokens(line)
        if not pending and ts and ts[0][0] == 'id' and ts[0][1].upper() == 'TEXT': text_start=lineno; continue
        if not ts: continue
        if not pending: pending_start=lineno
        continuation=bool(ts and ts[-1][1]==';')
        pending.extend(ts[:-1] if continuation else ts)
        if not continuation: logical.append((pending_start,lineno,pending)); pending=[]
    if pending: logical.append((pending_start,total,pending)); issue(pending_start,total,'Unterminated continuation')
    if text_start is not None: issue(text_start,total,'Unterminated TEXT block')
    for start,end,t in logical:
        if not t: continue
        u=[v.upper() for k,v in t]; first=u[0] if t[0][0] in ('id','punct') else ''; offset=0
        if any(k == 'unterminated_str' for k,v in t): issue(start,end,'Unterminated string literal; extraction may be incomplete')
        if first=='#':
            directive=u[1] if len(u)>1 else ''
            if directive in ('IF','IFDEF','IFNDEF'): conditional_depth+=1
            if directive=='ENDIF': conditional_depth=max(0,conditional_depth-1)
            issue(start,end,'Preprocessor directive retained; conditional branches are not evaluated')
            if directive=='INCLUDE' and len(t)>2: ref(t[2][1],'IMPORTS',start,reason='preprocessor include')
            continue
        if conditional_depth: issue(start,end,'Conditional compilation branch; runtime availability unknown')
        if first in ('HIDDEN','PROTECTED','PUBLIC') and len(u)>1 and u[1] in ('FUNCTION','PROCEDURE'): offset=1
        if t[offset][0] == 'id' and u[offset] in ('FUNCTION','PROCEDURE') and len(t)>offset+1 and t[offset+1][0]=='id':
            close_routine(start-1); short=t[offset+1][1]; name=(cls['name']+'.'+short) if cls else short
            routine={'name':name,'kind':'Method' if cls or owner else 'Function','start_line':start,'end_line':total,'owner':cls['name'] if cls else owner};symbols.append(routine); arrays[name.casefold()]=set();continue
        if first in ('FUNCTION','PROCEDURE') or (offset and u[offset] in ('FUNCTION','PROCEDURE')):
            issue(start,end,'Malformed routine declaration; definition not extracted')
            continue
        if first in ('ENDPROC','ENDPROCEDURE','ENDFUNC','ENDFUNCTION'): close_routine(end);continue
        if u[:2]==['DEFINE','CLASS'] and len(t)>2:
            close_routine(start-1)
            if cls is not None: cls['end_line']=start-1;issue(start,end,'Nested/unclosed class declaration')
            cls={'name':t[2][1],'kind':'Class','start_line':start,'end_line':total,'owner':owner};symbols.append(cls)
            if 'AS' in u and u.index('AS')+1<len(t):
                base=t[u.index('AS')+1][1];cls['base_class']=base
                library=t[u.index('OF')+1][1] if 'OF' in u and u.index('OF')+1<len(t) else ''
                ref(base,'INHERITS',start,library=library)
            continue
        if first=='ENDDEFINE':
            close_routine(start-1)
            if cls is not None: cls['end_line']=end;cls=None
            else: issue(start,end,'ENDDEFINE without class')
            continue
        if first=='WITH': with_depth+=1;issue(start,end,'WITH receiver is not statically resolved')
        elif first=='ENDWITH': with_depth=max(0,with_depth-1)
        scope=arrays.setdefault(context().casefold(),set())
        declaration = first in ('DIMENSION','DECLARE') or (first in ('LOCAL','PUBLIC','PRIVATE') and 'ARRAY' in u[:3])
        if declaration:
            for i,(kind,value) in enumerate(t[:-1]):
                if kind=='id' and t[i+1][1] in ('(', '['):scope.add(value.casefold())
        if first=='DO' and len(t)>1 and u[1] not in ('WHILE','CASE','EVENTS'):
            form=u[1]=='FORM'; idx=2 if form else 1
            if idx<len(t):
                target=t[idx][1];dynamic=target in ('&','(') or target.upper().startswith(('THIS.','THISFORM.'))
                if dynamic: target=''.join(v for k,v in t[idx:]);issue(start,end,'Dynamic DO target cannot be resolved')
                lib=t[u.index('IN')+1][1] if 'IN' in u and u.index('IN')+1<len(t) else ''
                ref(target,'OPENS_FORM' if form else 'CALLS',start,dynamic,library=lib)
        if u[:2] in (['SET','PROCEDURE'],['SET','CLASSLIB']) and 'TO' in u:
            targets = []; chunk = []
            for k,v in t[u.index('TO')+1:]:
                if v.upper() in ('ADDITIVE','ALIAS','IN'): break
                if v == ',':
                    if chunk: targets.append(chunk); chunk=[]
                else: chunk.append((k,v))
            if chunk: targets.append(chunk)
            for chunk in targets:
                target=''.join(v for k,v in chunk)
                dynamic=any(v in ('&','(') for k,v in chunk)
                ref(target,'IMPORTS',start,dynamic,'Macro/computed library' if dynamic else 'SET '+u[1])
                if dynamic: issue(start,end,'Macro/computed library target')
            continue
        if first=='&' or any(k=='punct' and v=='&' for k,v in t):
            ref(''.join(v for k,v in t),'CALLS',start,True,'Macro substitution; target unknown');issue(start,end,'Macro substitution requires runtime resolution')
        for i,(kind,value) in enumerate(t[:-1]):
            if kind!='id' or t[i+1][1]!='(':continue
            upper=value.upper(); lower=value.casefold()
            if lower in scope or lower in arrays.get('__program__',set()):continue
            if declaration:continue
            if upper in ('IF','WHILE','FOR','CASE','SCAN','IN','NOT','AND','OR'):continue
            leading_dot=i>0 and t[i-1][1]=='.'
            if leading_dot:value='.'+value
            dynamic=leading_dot or upper.startswith(('THIS.','THISFORM.')) or '.' in value
            reason='Receiver requires object/type resolution' if dynamic else ''
            if leading_dot and with_depth:reason='WITH receiver unresolved'
            if first in ('SELECT','INSERT','UPDATE','DELETE') and (first != 'SELECT' or 'FROM' in u):
                dynamic=True; reason='SQL expression; database/runtime resolution required'
            if upper in BUILTINS:reason='FoxPro builtin; do not resolve by user-symbol short name'
            if upper in ('EVALUATE','EXECSCRIPT'):
                dynamic=True;reason='Runtime expression/code evaluation';issue(start,end,reason)
            ref(value,'CALLS',start,dynamic,reason)
            if upper in ('CREATEOBJECT','NEWOBJECT'):
                if i+2<len(t) and t[i+2][0]=='str':
                    library = t[i+4][1] if upper == 'NEWOBJECT' and i+4<len(t) and t[i+3][1] == ',' and t[i+4][0] == 'str' else ''
                    ref(t[i+2][1],'INSTANTIATES',start,library=library)
                else:ref(value,'INSTANTIATES',start,True,'Computed class name')
    close_routine(total)
    if cls is not None:issue(cls['start_line'],total,'Class lacks ENDDEFINE')
    if with_depth:issue(1,total,'Unclosed WITH block')
    if conditional_depth:issue(1,total,'Unclosed conditional compilation block')
    return {'symbols':symbols,'references':refs,'issues':issues,'parser_version':PARSER_VERSION}
