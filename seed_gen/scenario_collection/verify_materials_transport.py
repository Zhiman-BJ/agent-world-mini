"""Real Kwant/tkwant fixtures; independent scattering, Rabi and Bessel oracles."""
from importlib.metadata import distribution, version
import json
from pathlib import Path
import traceback

import kwant
import numpy as np
from scipy.special import jv
import tkwant

OUT = Path('seed_gen/scenario_collection/runtime/materials_transport')
OUT.mkdir(parents=True, exist_ok=True)


def chain(length=15, barrier=0., bond=-1., open_system=True):
    lat = kwant.lattice.chain(norbs=1)
    builder = kwant.Builder()
    builder[(lat(i) for i in range(length))] = 0.
    builder[lat.neighbors()] = -1.
    builder[lat(length//2)] = barrier
    builder[lat(length//2), lat(length//2-1)] = bond
    if open_system:
        lead = kwant.Builder(kwant.TranslationalSymmetry((-1,)))
        lead[lat(0)] = 0.
        lead[lat.neighbors()] = -1.
        builder.attach_lead(lead)
        builder.attach_lead(lead.reversed())
    return builder.finalized(), lat


def static_once():
    energies = np.linspace(-1.5, 1.5, 13)
    wrong, _ = chain(barrier=2000)
    fixed, lat = chain(barrier=2)
    expected = (4-energies**2)/(4-energies**2+4)
    wrong_t = np.array([kwant.smatrix(wrong, e).transmission(1, 0) for e in energies])
    assert np.max(np.abs(wrong_t-expected)) > .49
    matrices = [kwant.smatrix(fixed, e) for e in energies]
    transmission = np.array([s.transmission(1, 0) for s in matrices])
    np.testing.assert_allclose(transmission, expected, atol=1e-12, rtol=0)
    for s in matrices:
        np.testing.assert_allclose(s.data.conj().T@s.data, np.eye(2), atol=1e-12, rtol=0)
        assert abs(s.transmission(0, 0)+s.transmission(1, 0)-1) < 1e-12
    dispersion = kwant.physics.Bands(fixed.leads[0])
    for k in np.linspace(-np.pi, np.pi, 11):
        np.testing.assert_allclose(dispersion(k), [-2*np.cos(k)], atol=1e-12, rtol=0)
    configuration = {'length':15, 'barrier':2., 'bond':-1., 'open_system':True}
    (OUT/'static_configuration.json').write_text(json.dumps(configuration, indent=2)+'\n', encoding='utf-8')
    restored, _ = chain(**json.loads((OUT/'static_configuration.json').read_text()))
    np.testing.assert_allclose([kwant.smatrix(restored, e).transmission(1, 0) for e in energies], transmission, atol=1e-12, rtol=0)

    weak, _ = chain(bond=-.25)
    failed = kwant.smatrix(weak, 0).transmission(1, 0)
    assert abs(failed-1) > .7
    pristine, _ = chain()
    state = kwant.wave_function(pristine, 0.3)(0)[0]
    bonds = [(lat(i+1), lat(i)) for i in range(14)]
    measured = kwant.operator.Current(pristine, where=bonds)(state)
    matrix = pristine.hamiltonian_submatrix()
    oracle = np.array([2*np.imag(state[i+1].conjugate()*matrix[i+1, i]*state[i]) for i in range(14)])
    np.testing.assert_allclose(measured, oracle, atol=1e-12, rtol=0)
    np.testing.assert_allclose(measured, np.ones(14), atol=1e-12, rtol=0)
    np.testing.assert_allclose(kwant.operator.Density(pristine)(state), abs(state)**2, atol=1e-12, rtol=0)
    np.testing.assert_allclose(kwant.operator.Source(pristine)(state), 0, atol=1e-12, rtol=0)
    assert abs(kwant.smatrix(pristine, .3).transmission(1, 0)-1) < 1e-12
    np.savez(OUT/'static_results.npz', energies=energies, transmission=transmission, current=measured)
    return {'max_transmission_error':float(np.max(abs(transmission-expected))),
            'wrong_barrier_transmission_at_zero':float(wrong_t[6]), 'repaired_transmission_at_zero':float(transmission[6]),
            'wrong_bond_transmission_at_zero':float(failed), 'repaired_current_min':float(min(measured)),
            'repaired_current_max':float(max(measured))}


def pulse_trace(amplitude):
    lat = kwant.lattice.chain(norbs=1)
    builder = kwant.Builder()
    builder[lat(0)] = builder[lat(1)] = 0.
    def hopping(site1, site2, time, amplitude):
        return -(1+amplitude*np.sin(time))
    builder[lat(1), lat(0)] = hopping
    system = builder.finalized()
    state = tkwant.onebody.WaveFunction.from_kwant(system, np.array([1., 0.], complex), params={'amplitude':amplitude})
    density = kwant.operator.Density(system)
    current = kwant.operator.Current(system, where=[(lat(1), lat(0))], sum=True)
    times = np.linspace(0, 2., 21)
    populations, currents = [], []
    for time in times:
        state.evolve(time)
        populations.append(state.evaluate(density))
        currents.append(state.evaluate(current))
    try:
        state.evolve(1.)
    except ValueError as exc:
        assert 'backwards' in str(exc)
    else:
        raise AssertionError('Backward state reuse must fail')
    return times, np.array(populations), np.array(currents)


def open_trace(open_system, buffer_cells=None):
    system, lat = chain(length=7, open_system=open_system)
    initial = np.zeros(7, complex)
    initial[3] = 1.
    boundaries = None
    if open_system:
        # Automatic selection returns only six cells here even at refl_max=1e-12.
        # Use an explicit lead buffer to control the broadband delta-packet tail.
        boundaries = (tkwant.leads.automatic_boundary(system.leads, tmax=6., refl_max=1e-12)
                      if buffer_cells is None else
                      [tkwant.leads.SimpleBoundary(buffer_cells, tmax=6.) for _ in system.leads])
    state = tkwant.onebody.WaveFunction.from_kwant(system, initial, boundaries=boundaries)
    density = kwant.operator.Density(system)
    current = kwant.operator.Current(system, where=[(lat(i+1), lat(i)) for i in range(6)])
    times = np.linspace(0, 6., 25)
    populations, currents = [], []
    for time in times:
        state.evolve(time)
        populations.append(state.evaluate(density))
        currents.append(state.evaluate(current))
    return times, np.array(populations), np.array(currents), [b.num_total_cells for b in boundaries or []]


def transient_once():
    times, wrong, _ = pulse_trace(.3)
    times, populations, currents = pulse_trace(.7)
    theta = times+.7*(1-np.cos(times))
    expected = np.sin(theta)**2
    assert np.max(abs(wrong[:,1]-expected)) > .3
    np.testing.assert_allclose(populations[:,1], expected, atol=2e-6, rtol=0)
    np.testing.assert_allclose(populations.sum(axis=1), 1., atol=2e-6, rtol=0)
    exact_current = (1+.7*np.sin(times))*np.sin(2*theta)
    np.testing.assert_allclose(currents, exact_current, atol=3e-6, rtol=0)
    configuration = {'amplitude':.7, 'hopping_unit':1., 'hbar':1.}
    (OUT/'pulse_configuration.json').write_text(json.dumps(configuration, indent=2)+'\n', encoding='utf-8')
    restored = json.loads((OUT/'pulse_configuration.json').read_text())
    _, replay, _ = pulse_trace(restored['amplitude'])
    np.testing.assert_allclose(replay, populations, atol=1e-10, rtol=0)

    times_open, closed, _, _ = open_trace(False)
    _, automatic, _, automatic_cells = open_trace(True)
    _, opened, open_current, repaired_cells = open_trace(True, buffer_cells=32)
    assert automatic_cells == [6, 6] and repaired_cells == [32, 32]
    indices = np.arange(7)-3
    exact_psi = (1j**indices)[None,:]*jv(indices[None,:], 2*times_open[:,None])
    exact_density = abs(exact_psi)**2
    assert np.max(abs(closed-exact_density)) > .1
    assert np.max(abs(automatic-exact_density)) > 1e-3
    np.testing.assert_allclose(opened, exact_density, atol=3e-6, rtol=0)
    exact_open_current = 2*np.imag(exact_psi[:,1:].conj()*(-1)*exact_psi[:,:-1])
    np.testing.assert_allclose(open_current, exact_open_current, atol=3e-6, rtol=0)
    assert opened[-1].sum() < .3 and abs(closed[-1].sum()-1) < 3e-6
    np.savez(OUT/'transient_results.npz', time=times, population=populations, current=currents,
             time_open=times_open, density_open=opened, current_open=open_current)
    return {'wrong_pulse_max_error':float(np.max(abs(wrong[:,1]-expected))),
            'pulse_population_error':float(np.max(abs(populations[:,1]-expected))),
            'pulse_current_error':float(np.max(abs(currents-exact_current))),
            'closed_boundary_error':float(np.max(abs(closed-exact_density))),
            'automatic_boundary_error':float(np.max(abs(automatic-exact_density))),
            'automatic_buffer_cells':automatic_cells, 'repaired_buffer_cells':repaired_cells,
            'open_density_error':float(np.max(abs(opened-exact_density))),
            'open_current_error':float(np.max(abs(open_current-exact_open_current))),
            'remaining_probability_at_time6':float(opened[-1].sum())}


def main():
    provenance = json.loads(distribution('tkwant').read_text('direct_url.json'))
    assert provenance['vcs_info']['commit_id'] == 'b2040b88d56b90288f1b6617e7f2070ec1db7ef5'
    backend = kwant.solvers.default.smodule.__name__
    assert backend == 'kwant.solvers.mumps'
    for sid, function, tasks, scope in [
        ('01.09.01', static_once,
         [('repair_barrier_and_conductance', 'Wrong2000 barrier fails; repair2 agrees with analytic impurity transmission, unitary S, lead dispersion and restore.'),
          ('repair_bond_and_current', 'Weak bond fails unit transmission; repair yields unit flux at all bonds and independent current/density/source checks.')],
         'One-orbital coherent 1D tight-binding chain, dimensionless hopping and e^2/h conductance. No material-calibrated device, disorder ensemble, interactions or spin.'),
        ('01.09.02', transient_once,
         [('repair_pulse_and_population', 'Actual time-dependent tkwant propagation agrees with commuting two-level analytic population/current; amplitude error, backwards-time rejection and configuration replay.'),
          ('repair_open_boundary', 'Closed chain and automatic six-cell buffers fail; repair to32 lead cells reproduces infinite-chain Bessel density/current; probability leaves central region.')],
         'Single-particle wavefunctions only. Two-site hopping pulse and infinite homogeneous chain from localized initial state. hbar=hopping=1; no many-body Fermi sea, self-consistency or calibrated voltage device.')]:
        first, second = function(), function()
        for key in first:
            np.testing.assert_allclose(first[key], second[key], atol=1e-10, rtol=0)
        report = {'scenario_id':sid, 'status':'passed', 'repeat_runs':2, 'repeat_equal':True,
                  'repeat_atol':1e-10, 'repeat_rtol':0, 'kwant_solver_backend':backend,
                  'versions':{name:version(name) for name in ('kwant','tkwant','numpy','scipy','python-mumps')},
                  'tkwant_source':provenance, 'metrics':first,
                  'tasks':[{'id':name,'status':'passed','assertions':[evidence]} for name,evidence in tasks],
                  'scope':scope}
        (OUT/(sid+'.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        target = OUT/f'attempt_failure_{len(list(OUT.glob("attempt_failure*.json")))+1:02d}.json'
        target.write_text(json.dumps({'status':'failed','traceback':traceback.format_exc()}, indent=2), encoding='utf-8')
        raise
