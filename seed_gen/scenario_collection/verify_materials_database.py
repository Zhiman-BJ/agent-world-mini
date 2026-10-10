"""Real JARVIS cache and structure APIs on explicitly synthetic local records."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import traceback
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import numpy as np
from jarvis.core.atoms import Atoms
from jarvis.db.figshare import data, get_db_info, get_request_data
from jarvis.db.jsonutils import dumpjson, loadjson
from pymatgen.core import Structure

OUT = Path('seed_gen/scenario_collection/runtime/materials_database')
OUT.mkdir(parents=True, exist_ok=True)


def make_records():
    """Assigned values test query semantics, not measured materials properties."""
    a = 5.43
    crystal = Atoms(lattice_mat=[[0, a/2, a/2], [a/2, 0, a/2], [a/2, a/2, 0]],
                    coords=[[0, 0, 0], [.25, .25, .25]], elements=['Si', 'Si'], cartesian=False)
    records = []
    for identifier, elements, gap, hull in [
        ('fixture-001', ['Si', 'Si'], 1.1, 0.),
        ('fixture-002', ['Ge', 'Ge'], .6, .01),
        ('fixture-003', ['Si', 'Si'], 1.5, .2),
        ('fixture-004', ['Ga', 'As'], 1.6, .02),
        ('fixture-005', ['Ga', 'As'], None, 0.),
    ]:
        structure = crystal.to_dict()
        structure['elements'] = elements
        records.append({'jid': identifier, 'bandgap_eV': gap, 'hull_eV_atom': hull,
                        'atoms': structure, 'provenance': 'synthetic_local_fixture_v1'})
    return records


def select(records, hull_cutoff):
    identifiers = [row['jid'] for row in records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('duplicate material identity')
    return sorted(row['jid'] for row in records
                  if row['bandgap_eV'] is not None and 1 <= row['bandgap_eV'] <= 2
                  and row['hull_eV_atom'] <= hull_cutoff)


def main():
    records = make_records()
    filename = 'synthetic_material_query_v1.json'
    cache_path = OUT/(filename+'.zip')
    # The custom name cannot be confused with an official JARVIS database cache.
    with ZipFile(cache_path, 'w', compression=ZIP_DEFLATED) as archive:
        info = ZipInfo(filename, date_time=(2026, 9, 17, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        archive.writestr(info, json.dumps(records, sort_keys=True))
    with patch('requests.get', side_effect=AssertionError('fixed task must not access network')):
        loaded = get_request_data(js_tag=filename, url='https://example.invalid/synthetic-fixture', store_dir=str(OUT))
        assert loaded == records
        catalog = get_db_info()
        assert 'dft_2d' in catalog and catalog['dft_2d'][1]=='d2-12-12-2022.json'
        try:
            data(dataset='not_a_dataset', store_dir=str(OUT))
        except ValueError as exc:
            assert str(exc)=='Check DB name options.'
        else:
            raise AssertionError('Unknown dataset was accepted')
    wrong = select(loaded, 50.)
    expected = ['fixture-001', 'fixture-004']
    assert wrong == ['fixture-001', 'fixture-003', 'fixture-004'] and wrong != expected
    repaired = select(loaded, 50/1000)
    assert repaired == expected
    try:
        select(loaded+[deepcopy(loaded[0])], .05)
    except ValueError as exc:
        assert str(exc)=='duplicate material identity'
    else:
        raise AssertionError('Duplicate cache identity was not rejected')
    selection = {'ids': repaired, 'gap_eV': [1, 2], 'hull_eV_atom': .05,
                 'dataset': 'synthetic_local_fixture_v1',
                 'cache_sha256': hashlib.sha256(cache_path.read_bytes()).hexdigest()}
    dumpjson(selection, str(OUT/'selection.json'))
    assert loadjson(str(OUT/'selection.json')) == selection

    entry = next(row for row in loaded if row['jid']=='fixture-001')
    bad_entry = deepcopy(entry)
    bad_entry['atoms']['cartesian'] = True
    wrong_structure = Atoms.from_dict(bad_entry['atoms']).pymatgen_converter()
    target_distance = 5.43*np.sqrt(3)/4
    wrong_distance = wrong_structure.get_distance(0, 1)
    assert abs(wrong_distance-target_distance) > 1
    native = Atoms.from_dict(entry['atoms'])
    structure = native.pymatgen_converter()
    assert isinstance(structure, Structure)
    assert structure.composition.get_el_amt_dict()=={'Si': 2.}
    assert abs(structure.volume-5.43**3/4) < 1e-10
    assert abs(structure.get_distance(0, 1)-target_distance) < 1e-12
    np.testing.assert_allclose(structure.frac_coords, [[0, 0, 0], [.25, .25, .25]], atol=1e-12)
    assert Atoms.from_dict(native.to_dict()).to_dict() == native.to_dict()
    dumpjson(structure.as_dict(), str(OUT/'structure.json'))
    restored = Structure.from_dict(loadjson(str(OUT/'structure.json')))
    assert restored==structure and abs(restored.get_distance(0, 1)-target_distance) < 1e-12
    assert entry['atoms']['cartesian'] is False
    report = {
        'scenario_id': '01.01.02', 'status': 'passed',
        'versions': {name: version(name) for name in ('jarvis-tools', 'pymatgen-core')},
        'tasks': [
            {'id': 'query_cache_and_repair_filter_units', 'status': 'passed', 'assertions': [
                'real JARVIS zip cache loader works with all network calls forbidden',
                'unknown dataset and duplicate identities rejected',
                '50eV/atom erroneously includes fixture003; 50meV converted to .05eV selects001/004',
                'missing gap excluded and query/cache provenance survives JSON']},
            {'id': 'repair_record_coordinates_and_bridge', 'status': 'passed', 'assertions': [
                'wrong cartesian flag fails fixed neighbor distance',
                'JARVIS to pymatgen preserves Si2, fractional coordinates, analytic cell volume and distance',
                'structure JSON restoration and source record unchanged']},
        ],
        'metrics': {'input_records': len(loaded), 'wrong_ids': wrong, 'selected_ids': repaired,
                    'wrong_distance_A': wrong_distance, 'distance_A': structure.get_distance(0, 1),
                    'target_distance_A': target_distance, 'volume_A3': structure.volume,
                    'cache_sha256': selection['cache_sha256']},
        'scope': 'Synthetic records with custom fixture IDs/cache filename, real JARVIS cached-file reader and pymatgen bridge. Figshare public endpoints returned403; no real database download, live Materials Project API/key, real property prediction or experimental data is claimed. Python filtering/identity and NumPy oracles are explicit infrastructure, not library query APIs.',
    }
    (OUT/'01.01.02.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report['metrics']), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        (OUT/'attempt_failure.json').write_text(json.dumps({'status': 'failed', 'traceback': traceback.format_exc()}, indent=2), encoding='utf-8')
        raise
