import struct
import tempfile
import unittest
from pathlib import Path
from foxpro_memory.extract import extract_file

def fixture(root,source=b'PROCEDURE Click\nENDPROC\n'):
    fields=[('UNIQUEID','C',10),('OBJNAME','M',4),('METHODS','M',4),('FLAG','L',1)]
    header=32+32*len(fields)+1;record_size=1+sum(f[2] for f in fields)
    dbf=bytearray(header+record_size);dbf[0]=0x30;struct.pack_into('<IHH',dbf,4,1,header,record_size);dbf[29]=3
    for i,(name,kind,width) in enumerate(fields):
        p=32+i*32;dbf[p:p+len(name)]=name.encode();dbf[p+11]=ord(kind);dbf[p+16]=width
    dbf[header-1]=13;dbf[header]=32;dbf[header+1:header+11]=b'ID00000001';struct.pack_into('<II',dbf,header+11,1,2)
    memo=bytearray(1536);struct.pack_into('>H',memo,6,512)
    for block,text in [(1,b'button'),(2,source)]:struct.pack_into('>II',memo,block*512,1,len(text));memo[block*512+8:block*512+8+len(text)]=text
    p=Path(root)/'test.scx';p.write_bytes(dbf);p.with_suffix('.sct').write_bytes(memo);return p

class ExtractTest(unittest.TestCase):
    def test_memo_provenance_and_nul_logical(self):
        with tempfile.TemporaryDirectory() as root:
            result=extract_file(fixture(root));self.assertEqual(result['status'],'indexed');unit=result['units'][0]
            self.assertEqual(unit['owner'],'button');self.assertEqual(unit['memo_block'],2);self.assertEqual(unit['byte_start'],1032);self.assertIn('PROCEDURE Click',unit['source'])
    def test_missing_memo_is_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            p=fixture(root);p.with_suffix('.sct').unlink();result=extract_file(p)
            self.assertEqual(result['status'],'partial');self.assertTrue(any('Missing memo' in x['reason'] for x in result['issues']))
    def test_memo_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            p=fixture(root);memo=bytearray(p.with_suffix('.sct').read_bytes());struct.pack_into('>I',memo,1028,999999);p.with_suffix('.sct').write_bytes(memo)
            result=extract_file(p);self.assertTrue(any('bounds' in x['reason'] for x in result['issues']))
    def test_business_dbf_opaque(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'customers.dbf';p.write_bytes(b'not a valid dbf');result=extract_file(p)
            self.assertEqual(result['status'],'opaque');self.assertEqual(result['units'],[])
