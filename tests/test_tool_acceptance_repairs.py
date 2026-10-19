"""Regression cases for the captured relation, mesh and optical handlers."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from env_gen.tool_gen.compiler import _contains

SPEC = importlib.util.spec_from_file_location(
    'acceptance_repairs', Path(__file__).resolve().parents[1] / 'scripts/repair_tool_contracts.py')
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)
ORIGINALS = json.loads((Path(__file__).parent / 'fixtures/tool_acceptance_handlers.json').read_text())


class Records:
    def __init__(self, tables):
        self.tables = tables

    def list(self, name, filters=None, limit=100, offset=0):
        rows = [r for r in self.tables.get(name, [])
                if all(r.get(k) == v for k, v in (filters or {}).items())]
        return rows[offset:offset + limit]

    def get(self, name, key):
        return next(iter(self.list(name, key)), None)


def execute(tool, arguments, context):
    namespace = {}
    exec(compile(tool['internal']['code'], tool['name'], 'exec'), namespace)
    return namespace['run'](arguments, context)


@pytest.mark.parametrize('name,collection,key,selector', [
    ('relate_calculations_host_id_to_hosts', 'calculations', 'calculation_id', 'host_id'),
    ('relate_calculations_configuration_id_to_configurations', 'calculations', 'calculation_id', 'configuration_id'),
    ('relate_calculations_reference_id_to_calculations', 'calculations', 'calculation_id', 'reference_id'),
    ('relate_configurations_host_id_to_hosts', 'configurations', 'configuration_id', 'host_id'),
])
def test_reverse_lookup_returns_matching_identifiers(name, collection, key, selector):
    original = ORIGINALS[name]['tool']
    rows = [{key: 'a', selector: 'selected'}, {key: 'b', selector: 'other'},
            {key: 'c', selector: 'selected'}]
    context = SimpleNamespace(records=Records({collection: rows}))
    expected = {'relation_count': 2, collection: [{key: 'a'}, {key: 'c'}]}
    assert not _contains(execute(original, {selector: 'selected'}, context)['data'], expected)
    fixed, rules = repair.repair_tool(original)
    assert rules == ['relation_reverse_record_identifiers']
    assert execute(fixed, {selector: 'selected'}, context)['data'] == expected
    assert execute(fixed, {selector: 'missing'}, context)['data'] == {
        'relation_count': 0, collection: []}
    assert repair.repair_tool(fixed) == (fixed, [])


def test_mesh_writes_readable_metadata_and_preserves_points(tmp_path):
    np = pytest.importorskip('numpy')
    original = ORIGINALS['k_mesh_from_samples']['tool']
    points = np.array([[0., 0.], [1., 1.]])
    np.save(tmp_path / 'source.npy', points)
    context = SimpleNamespace(scope_root=lambda scope: tmp_path, records=Records({
        'sampling_grids': [{'grid_id': 'grid', 'source_paths': ['source.npy'],
                            'dimension': 2, 'point_count': 2}]}))
    arguments = {'grid_id': 'grid', 'input_path': 'source.npy', 'output_path': 'mesh.npy'}
    before = (tmp_path / 'source.npy').read_bytes()
    result = execute(original, arguments, context)
    with pytest.raises(json.JSONDecodeError):
        json.loads((tmp_path / result['data']['metadata_path']).read_text())
    fixed, rules = repair.repair_tool(original)
    assert rules == ['mesh_metadata_json_newline']
    result = execute(fixed, arguments, context)
    assert result['success'] is True
    metadata = json.loads((tmp_path / result['data']['metadata_path']).read_text())
    assert metadata['point_count'] == 2 and metadata['dimension'] == 2
    np.testing.assert_array_equal(np.load(tmp_path / result['data']['output_path']), points)
    assert (tmp_path / 'source.npy').read_bytes() == before
    assert repair.repair_tool(fixed) == (fixed, [])


def test_optical_modes_match_kernels_and_preserve_numerical_result(tmp_path):
    np = pytest.importorskip('numpy')
    torch = pytest.importorskip('torch')
    image = pytest.importorskip('PIL.Image')
    original = ORIGINALS['simulate_openilt_aerial_image']['tool']
    image.fromarray(np.full((5, 5), 255, dtype=np.uint8)).save(tmp_path / 'mask.png')
    (tmp_path / 'model.txt').write_text('KernelNum 1\nDoseNom 1\nDoseMin 0.5\n')
    torch.save(torch.ones((3, 3, 1), dtype=torch.complex64), tmp_path / 'kernel.pt')
    torch.save(torch.ones(1), tmp_path / 'scale.pt')
    configs = [{'config_id': 'model', 'config_kind': 'text_parameters',
                'experiment_id': 'e', 'source_path': 'model.txt'}]
    configs += [{'config_id': mode + suffix, 'config_kind': 'torch_tensor_archive',
                 'experiment_id': 'e', 'source_path': path}
                for mode in ('focus', 'defocus')
                for suffix, path in (('', 'kernel.pt'), ('_scales', 'scale.pt'))]
    context = SimpleNamespace(scope_root=lambda scope: tmp_path, records=Records({
        'raster_masks': [{'raster_id': 'mask', 'source_path': 'mask.png'}],
        'optical_configs': configs}))

    def arguments(mode, kernels):
        return {'mask_raster_id': 'mask', 'model_config_id': 'model',
                'kernel_config_ids': [kernels, kernels + '_scales'], 'focus_condition': mode}

    assert execute(original, arguments('focus', 'defocus'), context)['success'] is True
    fixed, rules = repair.repair_tool(original)
    assert rules == ['optical_focus_condition_match']
    for mode, kernels in (('focus', 'defocus'), ('defocus', 'focus')):
        result = execute(fixed, arguments(mode, kernels), context)
        assert result['success'] is False
        assert result['error']['code'] == 'incompatible_model'
    for mode, intensity in (('focus', 1.), ('defocus', .25)):
        old = execute(original, arguments(mode, mode), context)
        new = execute(fixed, arguments(mode, mode), context)
        assert new == old
        # A constant mask has only a DC component; the unit kernel and scale
        # preserve its amplitude. Intensity is the square of the dose.
        np.testing.assert_allclose(new['data']['aerial_image'], intensity, atol=1e-6)
    assert repair.repair_tool(fixed) == (fixed, [])
