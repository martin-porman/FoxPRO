import unittest
from foxpro_memory.parser import parse_source

class ParserTests(unittest.TestCase):
    def parse(self, source): return parse_source(source)
    def names(self,result):return [x['name'] for x in result['symbols']]
    def calls(self,result):return [x['target'] for x in result['references'] if x['kind']=='CALLS']
    def test_do_targets(self):
        r=self.parse('DO worker IN helpers.prg WITH 1\nDO FORM invoice NAME o\nDO WHILE ok\nDO CASE')
        self.assertEqual([(x['target'],x['kind']) for x in r['references']],[('worker','CALLS'),('invoice','OPENS_FORM')]);self.assertEqual(r['references'][0]['library'],'helpers.prg')
    def test_set_procedure_not_definition(self):
        r=self.parse('SET PROCEDURE TO helpers ADDITIVE\nPROCEDURE main\nRETURN helper()\nENDPROC')
        self.assertEqual(self.names(r),['__program__','main']);self.assertEqual(r['references'][0]['kind'],'IMPORTS')
    def test_class_ranges_inheritance(self):
        r=self.parse('DEFINE CLASS Foo AS Bar\nPROTECTED PROCEDURE Init\nTHIS.work()\nENDPROC\nPROCEDURE work\nRETURN 1\nENDPROC\nENDDEFINE\nafter()')
        self.assertEqual(self.names(r),['__program__','Foo','Foo.Init','Foo.work']);self.assertEqual(r['symbols'][2]['end_line'],4)
        self.assertEqual(r['symbols'][1]['base_class'],'Bar');self.assertEqual(r['references'][-1]['source_name'],'__program__')
    def test_multiplication_preserves_call(self):self.assertEqual(self.calls(self.parse('RETURN 2 * expensive()')),['expensive'])
    def test_text_sql_is_opaque(self):
        r=self.parse('TEXT TO sql NOSHOW\nFUNCTION fake\nSELECT SUM(total) FROM x WHERE id IN (1,2)\nENDTEXT\nSQLEXEC(conn,sql)')
        self.assertEqual(self.names(r),['__program__']);self.assertEqual(self.calls(r),['SQLEXEC'])
    def test_arrays_not_calls(self):
        r=self.parse('DIMENSION values(10)\nx=values(1)\ny=helper(1)\nLOCAL ARRAY other[10]\nx=other(2)')
        self.assertEqual(self.calls(r),['helper'])
    def test_strings_comments(self):
        r=self.parse('NOTE fakecall()\n* fakecall()\nx=[fakecall()]\nRETURN [fakecall()]\nx="fakecall()" && fakecall()\nreal()')
        self.assertEqual(self.calls(r),['real'])
    def test_continuation_and_optional_end(self):
        r=self.parse('FUNCTION first\nDO worker ;\n IN lib.prg\nFUNCTION second\nRETURN helper( ;\n 1)')
        self.assertEqual(r['symbols'][1]['end_line'],3);self.assertEqual(r['references'][0]['library'],'lib.prg');self.assertEqual(r['references'][-1]['source_line'],5)
    def test_dynamic_and_builtin(self):
        r=self.parse('WITH thing\n.work()\nENDWITH\nDO &target\nx=EVALUATE(expr)\nx=CREATEOBJECT("Foo")\nx=NEWOBJECT(className)')
        self.assertTrue(r['references'][0]['dynamic']);self.assertTrue(any(x['target']=='Foo' and x['kind']=='INSTANTIATES' for x in r['references']))
        self.assertTrue(any('builtin' in x.get('reason','') for x in r['references']));self.assertTrue(r['issues'])
    def test_import_paths_and_macros(self):
        r=self.parse('SET PROCEDURE TO libs\\helpers.prg, &library ADDITIVE')
        self.assertEqual([x['target'] for x in r['references']],['libs\\helpers.prg','&library'])
        self.assertFalse(r['references'][0]['dynamic']);self.assertTrue(r['references'][1]['dynamic'])
    def test_newobject_library(self):
        r=self.parse('x=NEWOBJECT("Foo", "classes.vcx")')
        instance=[x for x in r['references'] if x['kind']=='INSTANTIATES'][0]
        self.assertEqual(instance['library'],'classes.vcx')
    def test_dll_declaration_is_not_routine(self):
        r=self.parse('DECLARE INTEGER external IN kernel32 STRING arg\nFUNCTION real\nRETURN PARAMETERS()')
        self.assertEqual(self.names(r),['__program__','real'])
    def test_doubled_quotes_and_string_keyword(self):
        r=self.parse('x="literal ""fake()"""\n"TEXT"\nreal()')
        self.assertEqual(self.calls(r),['real'])
    def test_malformed_declaration_and_string_reported(self):
        r=self.parse('FUNCTION\nx="unfinished')
        self.assertEqual(len(r['issues']),2)
    def test_do_receiver_unresolved(self):
        r=self.parse('DO THIS.worker')
        self.assertTrue(r['references'][0]['dynamic'])
    def test_preprocessor(self):
        r=self.parse('#IFDEF TEST\nFUNCTION conditional\nENDPROC\n#ENDIF');self.assertIn('conditional',self.names(r));self.assertTrue(r['issues'])
    def test_empty_and_owner(self):
        self.assertEqual(self.names(self.parse('')),['__program__'])
        r=parse_source('PROCEDURE Init\nENDPROC',owner='Form1');self.assertEqual(r['symbols'][1]['owner'],'Form1');self.assertEqual(r['symbols'][1]['kind'],'Method')
    def test_macro_date_literals_do_not_destroy_symbols(self):
        r=self.parse('FUNCTION a\nx={^2020-01-01}\nx=&expr\nx=BETWEEN(v, 0-maxvalue*0.1,maxvalue)\nFUNCTION b\nRETURN helper()')
        self.assertEqual(self.names(r),['__program__','a','b']);self.assertIn('helper',self.calls(r))

if __name__=='__main__':unittest.main()
