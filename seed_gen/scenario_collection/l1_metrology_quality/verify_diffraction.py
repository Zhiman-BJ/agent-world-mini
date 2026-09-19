"""Eight manufactured microscopy/simulation tasks, run in specified isolatedenvs."""
import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import numpy as np
from numpy.testing import assert_allclose,assert_array_equal
BASE=Path('seed_gen/scenario_collection/l1_metrology_quality/runtime/diffraction')
def result(a,b,oracle,fixture):return dict(tasks=[dict(id=a[0],status='passed',**a[1]),dict(id=b[0],status='passed',**b[1])],oracle=oracle,fixture=fixture)
def large_dataset():
    from libertem.api import Context
    from libertem.executor.inline import InlineJobExecutor
    from libertem.udf.sum import SumUDF
    from libertem.udf.sumsigudf import SumSigUDF
    data=np.arange(2*3*8*8,dtype=np.float32).reshape(2,3,8,8)
    with Context(executor=InlineJobExecutor()) as ctx:
        ds=ctx.load('memory',data=data,sig_dims=2,num_partitions=2)
        summed=ctx.run_udf(ds,SumUDF())['intensity'].data;image=ctx.run_udf(ds,SumSigUDF())['intensity'].data
        assert_array_equal(summed,data.sum((0,1)));assert_array_equal(image,data.sum((2,3)))
        roi=np.array([[1,0,0],[0,0,1]],dtype=bool);selected=ctx.run_udf(ds,SumUDF(),roi=roi)['intensity'].data;assert_array_equal(selected,data[roi].sum(0))
        wrong=ctx.run_udf(ds,SumUDF(),roi=~roi)['intensity'].data;assert not np.array_equal(wrong,selected)
        fixed=ctx.run_udf(ds,SumUDF(),roi=roi)['intensity'].data;assert_array_equal(fixed,selected)
    return result(('reduce_partitioned_stem_data',dict(navigation_shape=[2,3],detector_shape=[8,8],per_position_counts=image.tolist(),total_counts=float(summed.sum()))),
        ('repair_navigation_roi_mask',dict(selected_positions=[[0,0],[1,2]],selected_total=float(selected.sum()),wrong_total=float(wrong.sum()))),
        'Independentarange384cube; twoaxisreductionsandexplicitROIframes0/5sum separatelycomputedbyNumPy, notLiberTEM.',
        'Manufactured2x3x8x8memorydataset usingInlineexecutor; provespartition/ROIsemantics, notdistributedclusterorTBthroughput.')
def throughput():
    from libertem.api import Context
    from libertem.executor.inline import InlineJobExecutor
    from libertem.udf.sum import SumUDF
    from libertem.udf.sumsigudf import SumSigUDF
    data=np.arange(12*8*8,dtype='<f4').reshape(4,3,8,8);path=BASE/'detector.raw';data.tofile(path)
    with Context(executor=InlineJobExecutor()) as ctx:
        ds=ctx.load('raw',path=str(path),dtype='<f4',nav_shape=(4,3),sig_shape=(8,8),num_partitions=3)
        result_sum=ctx.run_udf(ds,SumUDF())['intensity'].data;assert_array_equal(result_sum,data.sum((0,1)))
        memory=ctx.load('memory',data=data,sig_dims=2,num_partitions=1)
        reference=ctx.run_udf(memory,SumUDF())['intensity'].data;assert_array_equal(result_sum,reference)
        wrong_ds=ctx.load('raw',path=str(path),dtype='>f4',nav_shape=(4,3),sig_shape=(8,8),num_partitions=3)
        try:
            wrong=ctx.run_udf(wrong_ds,SumSigUDF())['intensity'].data
        except NotImplementedError as exc:
            rejected_error=str(exc);assert 'byte swapping for floats not implemented' in rejected_error
        else:
            assert not np.allclose(wrong,data.sum((2,3)));rejected_error='Wrong-endian values violate independent per-frame sum'
        fixed=ctx.run_udf(ds,SumSigUDF())['intensity'].data;assert_array_equal(fixed,data.sum((2,3)))
    return result(('process_raw_detector_partitions',dict(bytes=path.stat().st_size,partition_count=3,total_counts=float(result_sum.sum()),memory_backend_equal=True)),
        ('repair_raw_data_endianness',dict(wrong_endian_rejected=True,rejected_error=rejected_error,fixed_position_counts=fixed.tolist())),
        'Knownlittleendianarange768file3072bytes; independentperframeandnavigation sums; onevsthreepartition/backendinvariance.',
        'ManufacturedsmallRAWfile, noTB-scaleIOorconcurrencybenchmark; logicalthroughputworkflowanddtype/shapecontractonly.')
def atomic_columns():
    import hyperspy.api as hs
    import atomap.api as am
    y,x=np.indices((64,64));pos=np.array([(a,b) for a in [16.2,32.3,48.1] for b in [16.1,32.2,48.3]])
    image=sum(np.exp(-((x-a)**2+(y-b)**2)/(2*1.3**2)) for a,b in pos)
    signal=hs.signals.Signal2D(image);found=am.get_atom_positions(signal,separation=7);assert len(found)==9
    def refine(points):
        sub=am.Sublattice(points,image=image);sub.find_nearest_neighbors();sub.refine_atom_positions_using_center_of_mass();sub.refine_atom_positions_using_2d_gaussian()
        values=np.asarray(sub.atom_positions);error=max(min(math.dist(p,q) for q in values) for p in pos);assert error<1e-4
        return sub,error
    lattice,error=refine(found)
    wrong=am.get_atom_positions(signal,separation=24);assert len(wrong)!=9
    fixed=am.get_atom_positions(signal,separation=7);repaired,error_fixed=refine(fixed)
    payload=dict(xy_pixels=np.asarray(repaired.atom_positions).tolist(),unit='pixel',source='manufactured9Gaussian');path=BASE/'atomic_columns.json';path.write_text(json.dumps(payload),encoding='utf-8');assert len(json.loads(path.read_text())['xy_pixels'])==9
    return result(('detect_and_refine_atomic_columns',dict(columns=9,max_position_error_pixel=float(error))),
        ('repair_peak_separation',dict(wrong_separation_pixels=24,wrong_detected_columns=len(wrong),fixed_separation_pixels=7,max_repaired_error_pixel=float(error_fixed))),
        'IndependentninefixedsubpixelGaussian centersCartesianproduct{16.2,32.3,48.1}x{16.1,32.2,48.3}; permutationinvariantnearest-distanceoracle<1e-4pixel.',
        'Noise-freeHAADF-likeGaussianfixture; positiononly, covariancewarningsnotinterpretedasvalidateduncertainty orrealatomicmetrology.')
def simulated_tem():
    import abtem
    from abtem.potentials.iam import PotentialArray
    from abtem.core.energy import energy2wavelength
    energy=200000.;h=6.62607015e-34;e=1.602176634e-19;m=9.1093837139e-31;c=299792458.
    gamma=1+e*energy/(m*c*c);wavelength=h/math.sqrt(2*m*e*energy*(1+e*energy/(2*m*c*c)))
    sigma=2*np.pi*m*gamma*e*wavelength/(h*h)*1e-10
    wave=abtem.PlaneWave(energy=energy,extent=(8,8),gpts=(32,32)).build(lazy=False)
    potential=PotentialArray(np.ones((2,32,32),dtype=np.float32)*10,slice_thickness=1,extent=(8,8))
    exit_wave=wave.multislice(potential);expected=np.exp(1j*sigma*20)
    assert_allclose(exit_wave.array,expected,atol=2e-7,rtol=1e-7);intensity=exit_wave.intensity().array;assert_allclose(intensity,1,atol=2e-6)
    assert_allclose(energy2wavelength(energy),wavelength*1e10,rtol=1e-7)
    wrong=energy2wavelength(200);assert wrong>wavelength*1e10*30
    fixed=energy2wavelength(200*1000);assert_allclose(fixed,wavelength*1e10,rtol=1e-7)
    return result(('simulate_constant_potential_tem',dict(energy_eV=energy,wavelength_A=float(wavelength*1e10),analytic_phase_rad=float(sigma*20),exit_value=[float(exit_wave.array[0,0].real),float(exit_wave.array[0,0].imag)],total_intensity=float(intensity.sum()))),
        ('repair_electron_energy_unit',dict(wrong_energy_eV=200,wrong_wavelength_A=float(wrong),fixed_wavelength_A=float(fixed))),
        'IndependentrelativisticlambdaandinteractionconstantfromSIconstants; uniform20V*A projectedpotentialgivesexp(i*sigma20), propagationofconstantphasepreservesunitintensity.',
        'Manufacturedconstantprojectedpotentialandplane wave; validatesmultislicephase/normalization/energyunits, noIAMvalencebondingorrealTEMagreement.')
def ebsd_processing():
    import kikuchipy as kp
    y,x=np.indices((8,8));features=(x+2*y).astype(np.uint8)*4;background=(30+x*2).astype(np.uint8)
    data=np.stack([features+background,np.flipud(features)+background]).astype(np.uint8).reshape(1,2,8,8)
    signal=kp.signals.EBSD(data);clean=signal.remove_static_background(static_bg=background,inplace=False,show_progressbar=False)
    expected=np.stack([features,np.flipud(features)]).astype(float);expected=(expected-expected.min(axis=(1,2),keepdims=True))/np.ptp(expected,axis=(1,2),keepdims=True)*255
    assert_allclose(clean.data.reshape(2,8,8),expected,atol=1)
    assert_array_equal(signal.data,data)
    rejected=False
    try:signal.remove_static_background(static_bg=background[:7],inplace=False,show_progressbar=False)
    except ValueError as exc:rejected=True;error=str(exc)
    assert rejected
    fixed=signal.remove_static_background(static_bg=background,inplace=False,show_progressbar=False);assert_array_equal(fixed.data,clean.data)
    return result(('correct_ebsd_pattern_background',dict(shape=list(clean.data.shape),output_range=[int(clean.data.min()),int(clean.data.max())],original_preserved=True)),
        ('repair_background_detector_shape',dict(rejected_error=error,repaired_shape=list(fixed.data.shape))),
        'Independentknownsignal+backgroundfixture; subtractionthenperpatternrescaleto0..255andintegerquantizationatol1, noabsolutecountconservationclaim.',
        'ManufacturedEBSD-likeintensitypatterns; noactualmasterpatternorgrainphaseidentification, retainrawpixelsbeforeinplaceoperations.')
def ebsd_indexing():
    import kikuchipy as kp
    from orix.crystal_map import CrystalMap
    from orix.quaternion import Rotation
    y,x=np.indices((8,8));patterns=np.array([x,y,((x+y)%3)*4],dtype=np.float32)
    rotation=Rotation.from_euler(np.deg2rad([[0,0,0],[30,0,0],[60,0,0]]))
    dictionary=kp.signals.EBSD(patterns);dictionary.xmap=CrystalMap(rotations=rotation,x=np.arange(3),y=np.zeros(3))
    observed=kp.signals.EBSD((patterns[[2,0,1]]*2+10).reshape(1,3,8,8))
    output=observed.dictionary_indexing(dictionary,keep_n=1,metric='ncc',dtype='float64')
    indices=np.asarray(output.prop['simulation_indices']).reshape(-1);assert_array_equal(indices,[2,0,1]);assert_allclose(output.rotations.data.reshape(3,4),rotation.data[[2,0,1]],atol=1e-12)
    wrong=dictionary.deepcopy();wrong.xmap=CrystalMap(rotations=rotation[[1,2,0]],x=np.arange(3),y=np.zeros(3))
    bad=observed.dictionary_indexing(wrong,keep_n=1,metric='ncc',dtype='float64');assert not np.allclose(bad.rotations.data.reshape(3,4),rotation.data[[2,0,1]])
    wrong.xmap=dictionary.xmap;fixed=observed.dictionary_indexing(wrong,keep_n=1,metric='ncc',dtype='float64');assert_allclose(fixed.rotations.data.reshape(3,4),rotation.data[[2,0,1]],atol=1e-12)
    return result(('match_ebsd_dictionary',dict(simulation_indices=indices.tolist(),scores=np.asarray(output.prop['scores']).reshape(-1).tolist(),quaternions=output.rotations.data.reshape(3,4).tolist())),
        ('repair_dictionary_orientation_binding',dict(wrong_metadata_rejected=True,repaired_indices=np.asarray(fixed.prop['simulation_indices']).reshape(-1).tolist())),
        'Knownobservedpatternpermutation[2,0,1]withpositivegain/offset; immutablequaternionlabelsattachedtooriginaldictionaryrowsmustbepropagated.',
        'Manufacturedthree-patternmatchingmechanicswithknownorientationlabels; patternsnotphysicalEBSDsimulations, noorientationaccuracyorrealphaserecoveryclaim.')
def diffraction_processing():
    import pyxem as px
    y,x=np.indices((32,32));image=np.exp(-((x-12)**2+(y-19)**2)/4)
    signal=px.signals.Diffraction2D(image[None,None]);shifts=signal.get_direct_beam_position(method='center_of_mass')
    assert_allclose(shifts.data,[[[4,-3]]],atol=1e-10)
    centered=signal.center_direct_beam(shifts=shifts,inplace=False);center=np.unravel_index(centered.data[0,0].argmax(),(32,32));assert center==(16,16)
    wrong_shifts=shifts.deepcopy();wrong_shifts.data*=-1;bad=signal.center_direct_beam(shifts=wrong_shifts,inplace=False);badcenter=np.unravel_index(bad.data[0,0].argmax(),(32,32));assert badcenter!=(16,16)
    fixed=signal.center_direct_beam(shifts=shifts,inplace=False);assert_array_equal(fixed.data,centered.data)
    fixed.calibration.scale=.05;fixed.calibration.units='k_A^-1';assert_allclose(fixed.axes_manager.signal_axes[0].scale,.05)
    return result(('center_electron_diffraction_beam',dict(original_center_yx=[19,12],correction_xy=shifts.data.reshape(2).tolist(),centered_yx=[int(v) for v in center])),
        ('repair_beam_shift_sign',dict(wrong_center_yx=[int(v) for v in badcenter],fixed_center_yx=[16,16],reciprocal_pixel_scale=.05)),
        'KnownGaussianbeamXY=(12,19)in32x32detectorrequires(+4,-3)xycorrectionto(16,16); repairdoesnotchangeoriginalpattern.',
        'Manufacturedsinglepatternbeamcentering/reciprocalcalibration; noBraggindexing,strainorellipticaldistortionruntimeclaim.')
def fourdstem():
    import py4DSTEM as p
    data=np.arange(2*3*8*8,dtype=float).reshape(2,3,8,8);cube=p.DataCube(data)
    mask=np.zeros((8,8),bool);mask[2:5,3:6]=True
    image=cube.get_virtual_image(mode='mask',geometry=mask,verbose=False);expected=np.sum(data[:,:,mask],axis=-1);assert_array_equal(image.data,expected)
    dp=cube.get_dp_mean();assert_array_equal(dp.data,data.mean((0,1)))
    cube.calibration.set_Q_pixel_size(.05);cube.calibration.set_Q_pixel_units('A^-1');cube.calibration.set_R_pixel_size(2);cube.calibration.set_R_pixel_units('nm')
    wrong=cube.get_virtual_image(mode='mask',geometry=mask.T,verbose=False,name='wrong');assert not np.array_equal(wrong.data,expected)
    fixed=cube.get_virtual_image(mode='mask',geometry=mask,verbose=False,name='fixed');assert_array_equal(fixed.data,expected)
    assert cube.calibration.get_Q_pixel_size()==.05 and cube.calibration.get_R_pixel_size()==2
    return result(('build_4dstem_virtual_detector',dict(virtual_image=image.data.tolist(),mean_dp_shape=list(dp.data.shape),Q_pixel_A_inverse=.05,R_pixel_nm=2)),
        ('repair_virtual_detector_axis_order',dict(wrong_virtual_image=wrong.data.tolist(),repaired_virtual_image=fixed.data.tolist())),
        'Independentarange384cubeandexplicitQx2:5,Qy3:6mask, NumPysumandmean; transposedmaskcannotpasssamecountsoracle.',
        'Manufactured2x3x8x8DataCube, fixedofficialv0.14.8sourceunderPython3.11; no4DSTEMptychographyorrealBraggdiskcalibrationclaim.')
FUNCTIONS={'07.01.03':(large_dataset,['libertem']),'07.02.04':(throughput,['libertem']),'07.02.02':(atomic_columns,['atomap','hyperspy']),'07.02.03':(simulated_tem,['abtem']),'07.03.01':(ebsd_processing,['kikuchipy','hyperspy']),'07.03.02':(ebsd_indexing,['kikuchipy','orix']),'07.03.03':(diffraction_processing,['pyxem','hyperspy']),'07.03.04':(fourdstem,['py4DSTEM'])}
def main():
    p=argparse.ArgumentParser();p.add_argument('--scene',choices=FUNCTIONS,required=True);a=p.parse_args();BASE.mkdir(parents=True,exist_ok=True)
    fn,packages=FUNCTIONS[a.scene];value=fn();value.update(scenario_id=a.scene,status='passed',versions={n:importlib.metadata.version(n) for n in packages+['numpy']},executed_script=Path(__file__).as_posix())
    (BASE/f'{a.scene}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8');print(a.scene,value['tasks'],flush=True)
if __name__=='__main__':main()
