import tempfile
from pathlib import Path
import unittest

from seed_gen.scripts.extract_transport_cython_refs import read_declarations, python_signature


class CythonTransportTests(unittest.TestCase):
    def test_typed_parameters_preserve_defaults_and_keyword_only(self):
        args, types=python_signature('self, const complex[::1] ket, object out=None, *, params=None')
        self.assertEqual(args,'self, ket, out=None, *, params=None')
        self.assertEqual(types,{'ket':'const complex[::1]','out':'object'})
        args,types=python_signature('self, np.ndarray[int, ndim=1] values, count=2')
        self.assertEqual(args,'self, values, count=2')
        self.assertEqual(types['values'],'np.ndarray[int, ndim=1]')

    def test_public_declarations_and_inherited_source_keep_docs(self):
        source='''cdef class _Base:
    def __call__(self, psi):
        """Apply the operator.

        Returns:
            float: expectation
        """
        pass
cdef class Density(_Base):
    """Density object."""
    def __init__(self, syst, *,
                 sum=False):
        pass
    cdef internal(self):
        pass
    cpdef double measure(self, double time) except -1:
        """Evaluate density."""
        pass
    property size:
        """Number of sites."""
        def __get__(self):
            return 1
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'native.pyx'
            path.write_text(source,encoding='utf-8')
            records, origins, initializers, classes=read_declarations(path,'example.native')
        self.assertEqual([r['name'] for r in records],['Density'])
        self.assertEqual([m['name'] for m in records[0]['function']],['__init__','measure','size'])
        self.assertEqual(records[0]['function'][1]['input']['time']['type'],'double')
        returned=classes['_Base']['function'][0]['output']
        self.assertEqual(next(iter(returned.values()))['description'],'expectation')
        self.assertEqual(next(iter(returned.values()))['type'],'float')
        self.assertIn('except -1:',origins['example.native.Density.measure']['declaration'])


if __name__=='__main__': unittest.main()
