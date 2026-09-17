"""HDLConvertor declaration boundaries and public alias regression checks."""
from pathlib import Path
import tempfile
import unittest
from seed_gen.scripts.extract_hdlconvertor_refs import extract_hdlconvertor


class HdlConvertorAdapterTests(unittest.TestCase):
    def fixture(self,root):
        pkg=root/'hdlConvertor'
        pkg.mkdir()
        (pkg/'__init__.py').write_text('from ._hdlConvertor import HdlConvertorPy as HdlConvertor, ParseException\n',encoding='utf-8')
        (pkg/'_hdlConvertor.pyx').write_text('''include "verilogPreproc.pyx"
cdef extern from "internal.h":
    cdef cppclass Internal:
        void parse()
cdef class ParseException(Exception):
    pass
cdef class HdlConvertorPy:
    """Parser prose."""
    cdef int private_state
    def __cinit__(self):
        pass
    def _internal(self):
        pass
    def parse(self, filenames, language, incdirs, debug=True):
        """:param filenames: input files
        :return: HdlContext instance
        """
        cdef int internal = 1
    def parse_str(self, text, language, incdirs):
        pass
    def verilog_pp(self, filename, lang):
        pass
    def verilog_pp_str(self, text, lang):
        pass
''',encoding='utf-8')
        (pkg/'verilogPreproc.pyx').write_text('''include "python_ver_independent_str.pyx"
cdef class CppStdMapProxy:
    def get(self, key, default=None):
        pass
    @property
    def count(self):
        pass
    @count.setter
    def count(self, value):
        pass
    cdef from_ptr(int* p):
        pass
''',encoding='utf-8')
        (pkg/'python_ver_independent_str.pyx').write_text('''if IS_PY3:
    def str_decode(s):
        return s.decode("utf-8")
    def str_encode(s):
        return s.encode("utf-8")
else:
    def str_decode(s):
        return s
    def str_encode(s):
        return s
''',encoding='utf-8')

    def test_reexports_docs_and_non_python_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.fixture(root)
            tools,files,meta=extract_hdlconvertor(root)
            parser=next(t for t in tools if t['name']=='HdlConvertor')
            self.assertEqual(parser['module'],'hdlConvertor')
            self.assertEqual([m['name'] for m in parser['function']],['parse','parse_str','verilog_pp','verilog_pp_str'])
            method=parser['function'][0]
            self.assertEqual(method['input']['filenames']['description'],'input files')
            self.assertIn('HdlContext',str(method['output']))
            self.assertFalse(any(t['name']=='Internal' for t in tools))
            self.assertEqual(len(meta['cython_initializers']),1)
            self.assertEqual(len(files),4)

    def test_getter_and_python3_branch_count_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.fixture(root)
            tools,_,_=extract_hdlconvertor(root)
            proxy=next(t for t in tools if t['name']=='CppStdMapProxy')
            self.assertEqual([m['name'] for m in proxy['function']],['get','count'])
            self.assertEqual(proxy['function'][1]['input'],{'self':None})
            self.assertEqual(sum(t['type']=='function' for t in tools),2)

    def test_missing_export_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.fixture(root)
            (root/'hdlConvertor/__init__.py').write_text('',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'re-export'):
                extract_hdlconvertor(root)


if __name__=='__main__':
    unittest.main()
