"""Check selected environments without installing packages or converting assets."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MARKER = 'RTX_SCAN_PREFLIGHT_JSON='


def probe(kind, gpu=False):
    if kind == 'planning':
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError('Use Python 3.12 for the supplied planning requirements')
        import numpy as np
        import scipy
        from pxr import Usd
        import warp as wp
        from filelock import FileLock
        with tempfile.TemporaryDirectory() as directory:
            with FileLock(str(Path(directory) / 'probe.lock'), timeout=2):
                pass
        result = dict(python=platform.python_version(), system=platform.system(),
                      architecture=platform.machine(), numpy=np.__version__,
                      scipy=scipy.__version__, warp=wp.__version__, usd=Usd.GetVersion(),
                      gpu_verified=False)
        if gpu:
            wp.init()
            device = wp.get_device('cuda:0')
            allocation = wp.zeros(8, dtype=wp.float32, device=device)
            wp.synchronize_device(device)
            assert np.array_equal(allocation.numpy(), np.zeros(8))
            result.update(gpu_verified=True, gpu=device.name)
        return result
    if kind == 'cad':
        completed = subprocess.run([sys.executable, '-m', 'usd_convert_cad', '--help'],
                                   capture_output=True, text=True, timeout=60)
        options = ['--convert-metadata', '--convert-curves', '--up-axis', '--filter-style',
                   '--load-hidden', '--material-type', '--instancing-style', '--tess-lod']
        missing = [option for option in options if option not in completed.stdout]
        if completed.returncode or missing:
            raise RuntimeError('CAD CLI unavailable or missing required options: '
                               + ', '.join(missing) + '\n' + completed.stderr[-1500:])
        return dict(version=importlib.metadata.version('usd-convert-cad'), required_options=options)
    if kind == 'isaac':
        from isaacsim import SimulationApp
        app = SimulationApp({'headless': True, 'enable_motion_bvh': True,
                             'extra_args': ['--/rtx-transient/stableIds/enabled=true']})
        try:
            from isaacsim.sensors.rtx import LidarRtx, get_gmo_data, apply_nonvisual_material
            for name in ['initialize', 'attach_annotator', 'get_current_frame', 'decode_stable_id_mapping']:
                if not hasattr(LidarRtx, name):
                    raise RuntimeError('Installed LidarRtx lacks ' + name)
            if not callable(get_gmo_data) or not callable(apply_nonvisual_material):
                raise RuntimeError('Required RTX decoding/material functions unavailable')
            for _ in range(3):
                app.update()
            try:
                version = importlib.metadata.version('isaacsim')
            except importlib.metadata.PackageNotFoundError:
                version = 'source/workstation build; inspect installed release'
            result = dict(python=platform.python_version(), isaac=version, sensor_api_available=True,
                          scope='Headless startup and API check only; no scan captured')
            # Some standalone Isaac launchers exit the process from app.close().
            # Publish the completed probe before shutdown, as capture.py does.
            print(MARKER + json.dumps(result), flush=True)
            return result
        finally:
            app.close()
    raise ValueError(kind)


def resolve_executable(value):
    candidate = Path(value).expanduser()
    if candidate.is_file():
        # Resolving a venv's Python symlink would run the base interpreter and
        # lose its installed dependencies. Make the path absolute, not canonical.
        return str(candidate.absolute())
    found = shutil.which(value)
    if found:
        return found
    raise FileNotFoundError('Executable not found: ' + value)


def run_probe(name, executable, timeout, gpu=False):
    try:
        executable = resolve_executable(executable)
        args = [executable, str(Path(__file__).resolve()), '--probe', name]
        if gpu:
            args.append('--gpu')
        completed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                                   errors='replace', timeout=timeout)
        messages = [line[len(MARKER):] for line in completed.stdout.splitlines()
                    if line.startswith(MARKER)]
        if completed.returncode or not messages:
            raise RuntimeError('Probe exited ' + str(completed.returncode) + ':\n'
                               + (completed.stdout + completed.stderr)[-3000:])
        return dict(name=name, status='passed', executable=executable, details=json.loads(messages[-1]))
    except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
        return dict(name=name, status='failed', error=str(exc))


def storage_check():
    target = Path(os.environ.get('BIMSCAN_DATA', ROOT / 'runtime/data')).expanduser().resolve()
    parent = target
    while not parent.exists():
        parent = parent.parent
    try:
        with tempfile.TemporaryDirectory(prefix='scan-preflight-', dir=parent):
            pass
        return dict(name='storage', status='passed', data_directory=str(target),
                    available_gib=round(shutil.disk_usage(parent).free / (1024**3), 1))
    except OSError as exc:
        return dict(name='storage', status='failed', error=str(exc))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpu', action='store_true', help='Exercise a real Warp CUDA allocation')
    parser.add_argument('--isaac', action='store_true', help='Start headless Isaac and check sensor API')
    parser.add_argument('--cad', action='store_true', help='Check the optional IFC-converter environment')
    parser.add_argument('--output', type=Path, help='Write structured results')
    parser.add_argument('--timeout', type=float, default=300, help='Per-probe timeout, not a scan timeout')
    parser.add_argument('--probe', choices=['planning', 'cad', 'isaac'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if args.probe:
        result = probe(args.probe, args.gpu)
        if args.probe != 'isaac':
            print(MARKER + json.dumps(result), flush=True)
        return 0
    planning = os.environ.get('BIMSCAN_PYTHON', sys.executable)
    checks = [run_probe('planning', planning, args.timeout, args.gpu), storage_check()]
    for name, requested, variable in [('cad', args.cad, 'BIMSCAN_CAD_PYTHON'),
                                      ('isaac', args.isaac, 'ISAAC_SIM_PYTHON')]:
        if requested:
            executable = os.environ.get(variable)
            if not executable:
                checks.append(dict(name=name, status='failed', error='Set ' + variable))
            else:
                checks.append(run_probe(name, executable, args.timeout))
        else:
            checks.append(dict(name=name, status='not_checked'))
    failed = any(check['status'] == 'failed' for check in checks)
    result = dict(status='failed' if failed else 'passed', checks=checks,
                  gpu_requested=args.gpu, isaac_requested=args.isaac,
                  capture_environment_checked=bool(args.gpu and args.isaac and not failed),
                  native_scan_verified=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
