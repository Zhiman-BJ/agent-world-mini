"""Small manufactured microscopy fixtures with independent coordinate/count oracles."""
import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import numpy as np
from numpy.testing import assert_allclose,assert_array_equal
import hyperspy.api as hs
import exspy

BASE=Path('seed_gen/scenario_collection/l1_metrology_quality/runtime/microscopy')

def result(a,b,oracle,fixture):
    return dict(tasks=[dict(id=a[0],status='passed',**a[1]),dict(id=b[0],status='passed',**b[1])],oracle=oracle,fixture=fixture)

def file_io():
    from rsciio.hspy import file_reader,file_writer
    data=np.arange(24,dtype=np.float64).reshape(2,3,4)
    axes=[dict(size=n,index_in_array=i,name=name,scale=scale,offset=offset,units=units,navigate=i<2)
          for i,(n,name,scale,offset,units) in enumerate([(2,'Y',2.,0.,'nm'),(3,'X',3.,1.,'nm'),(4,'energy',.5,10.,'eV')])]
    signal=dict(data=data,axes=axes,metadata={'General':{'title':'manufactured spectral image'},'Signal':{'signal_type':''},'Sample':{'sample_id':'W1'}},original_metadata={'source_id':'fixture-01'},tmp_parameters={},attributes={'_lazy':False})
    signal.update(package_info={'package':'manufactured_fixture','package_version':'1'},learning_results={},models={})
    path=BASE/'spectral_image.hspy';file_writer(str(path),signal,show_progressbar=False)
    loaded=file_reader(str(path))[0];assert_array_equal(loaded['data'],data)
    assert loaded['metadata']['Sample']['sample_id']=='W1' and loaded['original_metadata']['source_id']=='fixture-01'
    s=hs.signals.Signal1D(**loaded);axis=s.axes_manager.signal_axes[0]
    assert_allclose(axis.axis,[10,10.5,11,11.5],atol=0)
    wrong=s.deepcopy();wrong.axes_manager.signal_axes[0].scale=500
    assert not np.array_equal(wrong.axes_manager.signal_axes[0].axis,[10,10.5,11,11.5])
    wrong.axes_manager.signal_axes[0].scale=loaded['axes'][2]['scale'];assert_array_equal(wrong.data,data)
    assert_allclose(wrong.axes_manager.signal_axes[0].axis,[10,10.5,11,11.5],atol=0)
    return result(('roundtrip_microscopy_file',dict(shape=list(data.shape),total_counts=float(data.sum()),axis_eV=axis.axis.tolist())),
        ('repair_energy_scale_metadata',dict(wrong_scale=500,repaired_scale=.5,source_metadata_preserved=True)),
        'Independent arange24 cube, knownY/X/energy ordering and10+.5*k eV; file pixels and both metadata trees must survive.',
        'ManufacturedHSpy spectral image; demonstratesRosetta→HyperSpy bridge only, not allDM/EMD/TIFF vendors.')

def signal_model():
    data=np.arange(120,dtype=float).reshape(3,4,10);s=hs.signals.Signal1D(data)
    for a,name,scale in zip(s.axes_manager.navigation_axes,['X','Y'],[2.,3.]):a.name=name;a.scale=scale;a.units='nm'
    energy=s.axes_manager.signal_axes[0];energy.name='energy';energy.scale=.5;energy.offset=100;energy.units='eV'
    cropped=s.isig[101.:103.];assert_array_equal(cropped.data,data[:,:,2:6]);assert_allclose(cropped.axes_manager.signal_axes[0].axis,[101,101.5,102,102.5])
    # Navigation order isX,Y; ndarray orderY,X. Assert the chosen physical pixel independently.
    pixel=s.inav[2,1];assert_array_equal(pixel.data,data[1,2,:])
    image=s.sum(axis='energy');assert_array_equal(image.data,data.sum(axis=2))
    wrong=s.inav[1,2];assert not np.array_equal(wrong.data,data[1,2,:])
    s.save(BASE/'signal_model.hspy',overwrite=True);restored=hs.load(BASE/'signal_model.hspy');assert_array_equal(restored.inav[2,1].data,data[1,2,:])
    return result(('calibrate_and_slice_signal',dict(cropped_shape=list(cropped.data.shape),pixel_xy=[2,1],pixel_sum=float(pixel.data.sum()))),
        ('repair_navigation_axis_order',dict(wrong_xy=[1,2],fixed_xy=[2,1],hspy_roundtrip=True)),
        'NumPyY,X,E fixture givesXY=(2,1) channels60..69, sum645; cropE101..103 excludes endpoint and hasindices2..5.',
        'Manufactured3x4x10 spectrum image; explicit units, signal/navigation distinction and immutable source copy.')

def preprocessing():
    from skimage.registration import phase_cross_correlation
    from skimage.restoration import denoise_tv_chambolle
    y,x=np.indices((64,64));truth=((x>15)&(x<45)&(y>20)&(y<49)).astype(float)
    truth+=.5*np.exp(-((x-48)**2+(y-13)**2)/12)
    noise=.08*((x+y)%2*2-1);noisy=truth+noise
    filtered=denoise_tv_chambolle(noisy,weight=.08,channel_axis=None)
    before=float(np.mean((noisy-truth)**2));after=float(np.mean((filtered-truth)**2));assert after<before/4
    moving=np.roll(truth,(3,-4),axis=(0,1));shift,_,_=phase_cross_correlation(truth,moving,upsample_factor=1)
    assert_array_equal(shift,[-3,4])
    wrong=np.roll(moving,tuple((-shift).astype(int)),axis=(0,1));assert np.mean((wrong-truth)**2)>.01
    repaired=np.roll(moving,tuple(shift.astype(int)),axis=(0,1));assert_allclose(repaired,truth,atol=0)
    signal=hs.signals.Signal2D(repaired);signal.axes_manager.signal_axes.set(scale=.25,units='nm')
    crop=signal.deepcopy();crop.crop_signal(left=4,right=12,top=6,bottom=14)
    # Integer crop coordinates denote pixelindices, unlike floating physical bounds.
    assert_array_equal(crop.data,truth[6:14,4:12])
    return result(('denoise_and_register_image',dict(noisy_mse=before,denoised_mse=after,estimated_yx_shift=shift.tolist())),
        ('repair_alignment_sign',dict(wrong_sign_rejected=True,repaired_max_error=float(abs(repaired-truth).max()),crop_shape=list(crop.data.shape))),
        'Knownintegerroll(+3,-4) requires(-3,+4); independent cleanimage MSE frozen beforefilter and exact repairedpixel equality.',
        'ManufacturedSEM-like contrast shapes with deterministiccheckerboard noise; not atomic-resolution metrology accuracy.')

def eds_signal(counts):
    data=np.zeros(np.shape(counts)[:-1]+(1000,),dtype=float)
    data[...,149]=np.asarray(counts)[...,0];data[...,174]=np.asarray(counts)[...,1]
    s=exspy.signals.EDSTEMSpectrum(data)
    axis=s.axes_manager.signal_axes[0];axis.scale=.01;axis.offset=0;axis.units='keV';axis.name='X-ray energy'
    s.set_microscope_parameters(beam_energy=200,live_time=1)
    s.add_elements(['Al','Si']);s.add_lines(['Al_Ka','Si_Ka'])
    return s

def eds():
    s=eds_signal([100,200]);lines=s.get_lines_intensity(['Al_Ka','Si_Ka'],integration_windows=[[1.47,1.51],[1.72,1.76]])
    counts=[float(q.data[0]) for q in lines];assert_allclose(counts,[100,200],atol=0)
    composition=s.quantification(lines,method='CL',factors=[1,2],composition_units='weight',plot_result=False)
    fractions=[float(q.data[0]) for q in composition];assert_allclose(fractions,[20,80],atol=1e-12)
    wrong=s.quantification(lines,method='CL',factors=[2,1],composition_units='weight',plot_result=False)
    assert_allclose([float(q.data[0]) for q in wrong],[50,50],atol=1e-12)
    fixed=s.quantification(lines,method='CL',factors=[1,2],composition_units='weight',plot_result=False)
    assert_allclose([float(q.data[0]) for q in fixed],[20,80],atol=1e-12)
    return result(('integrate_and_quantify_eds',dict(line_counts=counts,weight_percent=fractions)),
        ('repair_line_factor_order',dict(wrong_weight_percent=[50,50],fixed_weight_percent=[20,80])),
        'Independentdelta-channel sums100/200; weight fractionsI*k/sum(I*k) give20/80 withfactors1/2.',
        'ManufacturedAl/Si ideal nonoverlapping peaks, fixedCL factors; noabsorption/thickness correction or realcomposition validation.')

def eels():
    data=np.zeros((2,256));data[:,20]=[80,50];data[:,100]=[20,50]
    s=exspy.signals.EELSSpectrum(data);a=s.axes_manager.signal_axes[0];a.offset=-10.;a.scale=.5;a.units='eV'
    center=s.estimate_zero_loss_peak_centre();assert_allclose(center.data,[0,0],atol=0)
    thickness=s.estimate_thickness(threshold=5.);expected=np.log([100/80,100/50])
    assert_allclose(thickness.data,expected,atol=1e-12)
    wrong=s.estimate_thickness(threshold=45.);assert_allclose(wrong.data,[0,0],atol=1e-12)
    fixed=s.estimate_thickness(threshold=5.);assert_allclose(fixed.data,expected,atol=1e-12)
    return result(('estimate_eels_relative_thickness',dict(zlp_centers_eV=center.data.tolist(),t_over_lambda=thickness.data.tolist())),
        ('repair_zero_loss_window',dict(wrong_threshold_eV=45,wrong_relative_thickness=wrong.data.tolist(),fixed_threshold_eV=5)),
        'Independentlog(totalcounts/ZLPcounts) givesln1.25 andln2; peakposition(-10+.5*20)=0eV; threshold45wronglyincludesinelasticpeak40eV.',
        'Manufacturedbinned EELS two-delta spectra; onlyrelative thickness, noabsolute meanfreepath/angularcorrection or edgecrosssection claim.')

def spectrum_image():
    al=np.array([[10,20,30],[40,50,60.]])
    s=eds_signal(np.stack([al,100-al],axis=-1));s.axes_manager.navigation_axes.set(name=('X','Y'),scale=.5,units='nm')
    lines=s.get_lines_intensity(['Al_Ka','Si_Ka'],integration_windows=[[1.47,1.51],[1.72,1.76]])
    maps=s.quantification(lines,method='CL',factors=[1,1],composition_units='weight',plot_result=False)
    assert_allclose(maps[0].data,al,atol=1e-12);assert_allclose(maps[1].data,100-al,atol=1e-12)
    wrong=maps[0].data.T;assert wrong.shape!=(2,3)
    saved=BASE/'composition.hspy';maps[0].save(saved,overwrite=True)
    restored=hs.load(saved);assert_allclose(restored.data,al,atol=1e-12)
    assert restored.axes_manager.navigation_axes[0].name=='X'
    return result(('build_composition_map',dict(al_weight_percent=maps[0].data.tolist(),sum_percent=(maps[0].data+maps[1].data).tolist())),
        ('repair_composition_map_orientation',dict(wrong_shape=list(wrong.shape),restored_shape=list(restored.data.shape),pixel_y1_x2_percent=float(restored.data[1,2]))),
        'Manufactured2row3column Acounts10..60 and B100-A,factors1/1 giveexactAweightpercentage; pixelY1X2=60.',
        'Manufactured spectrumimage; knownaxisand compositionmap oracle, noexperimental quantification claim.')

FUNCTIONS={'07.01.01':file_io,'07.01.02':signal_model,'07.02.01':preprocessing,'07.04.01':eds,'07.04.02':eels,'07.04.03':spectrum_image}
def main():
    p=argparse.ArgumentParser();p.add_argument('--scene',choices=FUNCTIONS);args=p.parse_args();BASE.mkdir(parents=True,exist_ok=True)
    for sid,fn in FUNCTIONS.items():
        if args.scene and args.scene!=sid:continue
        value=fn();value.update(scenario_id=sid,status='passed',versions={n:importlib.metadata.version(n) for n in ['hyperspy','rosettasciio','exspy','scikit-image','numpy']},executed_script=Path(__file__).as_posix())
        (BASE/f'{sid}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        print(sid,value['tasks'],flush=True)
if __name__=='__main__':main()
