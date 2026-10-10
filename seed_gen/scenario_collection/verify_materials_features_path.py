"""Fixed descriptor and band-path fixtures with analytic independent checks."""
from __future__ import annotations
import importlib.metadata
import json
from pathlib import Path

import numpy as np
import pandas as pd
from matminer.featurizers.composition import ElementFraction, ElementProperty, Stoichiometry
from matminer.featurizers.conversions import StrToComposition
from matminer.utils.data import PymatgenData
from pymatgen.core import Composition, Lattice, Structure
import seekpath

OUT = Path('seed_gen/scenario_collection/runtime/materials_features_path')


def save(sid, tasks, metrics):
    result = {'scenario_id':sid,'status':'passed','fixture_version':1,
              'versions':{p:importlib.metadata.version(p) for p in ('pymatgen-core','matminer','seekpath','spglib','numpy','pandas')},
              'tasks':tasks,'metrics':metrics,'scope':'Local fixed features and geometrical k paths; no trained ML model, experimental property prediction or DFT band calculation.'}
    (OUT/f'{sid}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(sid,json.dumps(metrics))


def maximum_step(path):
    points = path['explicit_kpoints_abs']
    return max(float(np.max(np.linalg.norm(np.diff(points[start:stop],axis=0),axis=1)))
               for start,stop in path['explicit_segments'] if stop-start>1)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    converter = StrToComposition()
    compositions = [converter.featurize(f)[0] for f in ('GaAs','AlAs','Si')]
    elemental = ElementFraction()
    fractions = np.array([elemental.featurize(c) for c in compositions])
    np.testing.assert_allclose(fractions.sum(axis=1),1,atol=1e-14)
    np.testing.assert_allclose(fractions[0,[30,32]],[0.5,0.5],atol=1e-14)
    descriptor = ElementProperty(data_source=PymatgenData(impute_nan=False),features=['Z'],stats=['mean','minimum','maximum'],impute_nan=False)
    features = np.array([descriptor.featurize(c) for c in compositions])
    expected = np.array([[32,31,33],[23,13,33],[14,14,14]])
    np.testing.assert_allclose(features,expected,atol=1e-12)
    assert list(np.argsort(features[:,0])) == [2,1,0]
    labels = descriptor.feature_labels()
    frame = pd.DataFrame({'composition':compositions})
    descriptor.set_n_jobs(1)
    generated = descriptor.featurize_dataframe(frame,'composition',pbar=False)
    np.testing.assert_allclose(generated[labels].to_numpy(),expected,atol=1e-12)
    generated[labels].to_csv(OUT/'composition_features.csv',index=False)
    wrong = Composition('Ga2As')
    wrong_value = descriptor.featurize(wrong)[0]
    assert not np.isclose(wrong_value,32,atol=1e-12)
    corrected = converter.featurize('GaAs')[0]
    np.testing.assert_allclose(descriptor.featurize(corrected)[0],32,atol=1e-12)
    # Normalize counts before comparison; doubled formula is physically equivalent.
    np.testing.assert_allclose(elemental.featurize(Composition('Ga2As2')),elemental.featurize(corrected),atol=1e-12)
    stoich = Stoichiometry(p_list=[2])
    np.testing.assert_allclose(stoich.featurize(corrected)[0],np.sqrt(0.5),atol=1e-12)
    save('01.01.03',[
        {'id':'semiconductor_composition_features','status':'passed','assertions':['element fractions sum to 1','Ga/As fractions each .5','independent Z mean/min/max','stable ordering Si AlAs GaAs','batch and single results agree']},
        {'id':'repair_feature_composition','status':'passed','assertions':['Ga2As fails fixed GaAs mean-Z target','GaAs repair passes','Ga2As2 normalized features equal GaAs','p2 stoichiometry equals sqrt(.5)']},
    ],{'feature_matrix':features.tolist(),'labels':labels,'wrong_Ga2As_mean_Z':float(wrong_value),'target_GaAs_mean_Z':32.0})
    a = 5.431
    silicon = Structure.from_spacegroup(227,Lattice.cubic(a),['Si'],[[0,0,0]])
    cell = silicon.lattice.matrix,silicon.frac_coords,silicon.atomic_numbers
    path = seekpath.get_path(cell,symprec=1e-5)
    assert path['spacegroup_number']==227 and path['bravais_lattice']=='cF'
    reciprocal = np.array(path['reciprocal_primitive_lattice'])
    primitive = np.array(path['primitive_lattice'])
    np.testing.assert_allclose(primitive@reciprocal.T,2*np.pi*np.eye(3),atol=1e-12)
    np.testing.assert_allclose(abs(np.linalg.det(primitive)),a**3/4,rtol=1e-12)
    np.testing.assert_allclose(path['point_coords']['GAMMA'],[0,0,0],atol=1e-12)
    x_norm = np.linalg.norm(np.array(path['point_coords']['X'])@reciprocal)
    l_norm = np.linalg.norm(np.array(path['point_coords']['L'])@reciprocal)
    np.testing.assert_allclose(x_norm,2*np.pi/a,atol=1e-12)
    np.testing.assert_allclose(l_norm,np.sqrt(3)*np.pi/a,atol=1e-12)
    coarse = seekpath.get_explicit_k_path(cell,reference_distance=0.5,symprec=1e-5)
    coarse_step = maximum_step(coarse)
    assert coarse_step>0.05
    fine = seekpath.get_explicit_k_path(cell,reference_distance=0.02,symprec=1e-5)
    fine_step = maximum_step(fine)
    assert fine_step<0.05
    np.testing.assert_allclose(np.array(fine['explicit_kpoints_rel'])@reciprocal,fine['explicit_kpoints_abs'],atol=1e-12)
    np.savez(OUT/'silicon_kpath.npz',relative=fine['explicit_kpoints_rel'],absolute=fine['explicit_kpoints_abs'],segments=fine['explicit_segments'],primitive=primitive)
    save('01.02.02',[
        {'id':'silicon_standard_kpath','status':'passed','assertions':['SG227 FCC','primitive volume a^3/4','A B transpose equals 2pi I','Gamma zero','X norm=2pi/a','L norm=sqrt3*pi/a']},
        {'id':'repair_kpath_sampling','status':'passed','assertions':['coarse path fails fixed maximum step .05 reciprocal Angstrom','fine path passes same bound','relative times reciprocal basis equals absolute coordinates']},
    ],{'spacegroup':227,'X_norm_inv_A':float(x_norm),'L_norm_inv_A':float(l_norm),'coarse_max_step_inv_A':coarse_step,'fine_max_step_inv_A':fine_step,'fine_points':len(fine['explicit_kpoints_abs'])})


if __name__=='__main__':
    main()
