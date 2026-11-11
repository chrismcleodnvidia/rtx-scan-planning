"""Acquire, compare and combine additional native RTX scans without changing USD."""
from pathlib import Path
import argparse, re, shutil, time, traceback
import numpy as np
from core import read, write, project, digest, scene_path, PROFILES


def identifier(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid run identifier: ' + value)
    return value


def capture_batch(args):
    from pipeline import scan
    density = read(args.plan)
    folder = project(density['dataset'])
    base = folder / 'runs' / identifier(density['base_run'])
    source = read(base / 'capture.json')
    original = read(base / 'stations.json')
    if density['source_revision'] != read(folder / 'project.json')['source_revision']:
        raise ValueError('Source revision changed')
    profile = PROFILES[original['profile']]
    config = source['sensor_config']
    for key, value in [('numberOfChannels', profile['channels']),
                       ('reportRateBaseHz', profile['report_rate']),
                       ('nearRangeM', profile['near']), ('farRangeM', profile['far'])]:
        if not np.isclose(config[key][1], value):
            raise ValueError('Configured profile differs from the recorded baseline: ' + key)
    if source['elevation_degrees'] != profile['elevation']:
        raise ValueError('Configured elevation differs from the recorded baseline')
    name = identifier(args.run)
    stations = []
    for entry in density['stations'][args.start:args.stop]:
        row = dict(entry)
        row['density_plan_station_id'] = identifier(row['id'])
        row['id'] = name + '_' + row['id']
        stations.append(row)
    if not stations:
        raise ValueError('Empty capture batch')
    if args.dry_run:
        print('READY', density['dataset'], name, len(stations), original['profile'], flush=True)
        return
    run = folder / 'runs' / name
    state_path = args.state_dir / (name + '-state.json')
    state = dict(dataset=density['dataset'], base_run=base.name, run=name,
                 positions=len(stations), start_index=args.start, stop_index=args.stop,
                 density_plan=str(args.plan.resolve()), density_plan_sha256=digest(args.plan),
                 status='preparing', started=time.time())
    write(state_path, state)
    try:
        if not run.exists():
            run.mkdir()
            plan = dict(original)
            plan.pop('aggregation', None)
            plan.update(stations=stations, element_visibility=[],
                        candidate_count=density['candidate_count'],
                        candidate_reachable_area_m2=None, selected_visible_area_m2=None,
                        density_plan={k:v for k,v in density.items() if k != 'stations'})
            plan['assumptions'] = list(original['assumptions']) + [
                'Additional density-selected views; reported coverage uses native captures and the unchanged audit.']
            write(run / 'stations.json', plan)
            shutil.copy2(base / 'survey-brief.json', run / 'survey-brief.json')
            write(run / 'capture-role.json', dict(role='supplement', base_run=base.name))
        elif read(run / 'stations.json')['stations'] != stations:
            raise ValueError('Existing run contains different positions; choose a new run name')
        state['status'] = 'capturing_and_measuring'; write(state_path, state)
        if not (run / 'metrics.json').exists():
            scan(density['dataset'], original['profile'], name, publish=False)
        result = read(run / 'capture.json')
        for key in ['source_revision', 'source_scene_sha256', 'sensor_config', 'elevation_degrees']:
            if result[key] != source[key]:
                raise ValueError('Capture configuration changed: ' + key)
        state.update(status='complete', finished=time.time(),
                     measured_support=read(run / 'metrics.json')['requested_support'])
    except Exception:
        state.update(status='failed', error=traceback.format_exc()); write(state_path, state)
        raise
    write(state_path, state)
    print('REFINEMENT_CAPTURE_COMPLETE', name, len(stations), flush=True)


def compare_runs(dataset, names):
    folder = project(dataset)
    if len(names) != len(set(names)):
        raise ValueError('A capture was listed more than once')
    runs = [folder / 'runs' / identifier(name) for name in names]
    captures = [read(run / 'capture.json') for run in runs]
    if captures[0]['source_revision'] != read(folder / 'project.json')['source_revision']:
        raise ValueError('Dataset source revision changed')
    if captures[0]['source_scene_sha256'] != digest(scene_path(folder)):
        raise ValueError('Scan composition changed')
    for path, expected in captures[0].get('source_usd_layers', {}).items():
        if digest(Path(path)) != expected:
            raise ValueError('Source USD layer changed: ' + path)
    for capture in captures:
        if capture['validation_status'] != 'passed_capture_checks':
            raise ValueError('Capture incomplete')
        for key in ['source_revision', 'source_scene_sha256', 'source_usd_layers', 'sensor_config', 'elevation_degrees']:
            if capture[key] != captures[0][key]:
                raise ValueError('Incompatible captures: ' + key)
    ids = [s['id'] for c in captures for s in c['stations']]
    if len(ids) != len(set(ids)):
        raise ValueError('Capture station identifiers overlap')
    audits = [np.load(run / 'coverage-audit.npz') for run in runs]
    sample = np.load(folder / 'geometry/audit.npz')
    if any(len(a['nearest']) != len(sample['points']) for a in audits):
        raise ValueError('Coverage sample counts differ')
    nearest = np.minimum.reduce([a['nearest'] for a in audits])
    visible = np.logical_or.reduce([a['visible'] for a in audits])
    criteria = read(runs[0] / 'metrics.json')['requested_support']
    tolerance = criteria['tolerance_m']
    labels = read(folder / 'geometry/labels.json')
    objects = []
    for label in labels:
        own = sample['label_index'] == label['index']; selected = own & visible
        missing = selected & (nearest > tolerance)
        objects.append(dict(guid=label['guid'], name=label['name'], ifc_class=label.get('ifc_class'),
                            all_samples=int(own.sum()), visible_samples=int(selected.sum()),
                            unsupported_samples=int(missing.sum()),
                            visible_support=float(np.mean(nearest[selected] <= tolerance)) if selected.any() else None,
                            unsupported_visible_area_m2=float(missing.sum() * sample['sample_area_m2'])))
    objects.sort(key=lambda x:x['unsupported_visible_area_m2'], reverse=True)
    classes = {}
    for item in objects:
        group = classes.setdefault(item['ifc_class'], dict(ifc_class=item['ifc_class'],
            visible_samples=0, unsupported_samples=0, unobserved_sampled_elements=0))
        group['visible_samples'] += item['visible_samples']
        group['unsupported_samples'] += item['unsupported_samples']
        group['unobserved_sampled_elements'] += int(item['all_samples'] > 0 and item['visible_samples'] == 0)
    for group in classes.values():
        group['visible_support'] = (1 - group['unsupported_samples'] / group['visible_samples']) if group['visible_samples'] else None
    classes = sorted(classes.values(), key=lambda g:g['unsupported_samples'], reverse=True)
    fraction = float(np.mean(nearest[visible] <= tolerance))
    return dict(dataset=dataset, runs=names, source_revision=captures[0]['source_revision'],
                audit_sha256=digest(folder / 'geometry/audit.npz'), tolerance_m=tolerance,
                target=criteria['target'], target_met=fraction >= criteria['target'],
                positions=len(ids), returns=sum(s['points'] for c in captures for s in c['stations']),
                before_visible_support=float(np.mean(audits[0]['nearest'][audits[0]['visible']] <= tolerance)),
                before_visible_samples=int(audits[0]['visible'].sum()),
                visible_support=fraction, visible_samples=int(visible.sum()),
                all_surface_support=float(np.mean(nearest <= tolerance)),
                classes=classes, objects=objects,
                scope='Actual native-return audits; matching IFC identity required. Source geometry unchanged.')


def combine_runs(args):
    from merge_runs import merge
    folder = project(args.dataset)
    measured = compare_runs(args.dataset, args.runs)
    output = identifier(args.output_run)
    merge(folder, args.runs, output, args.title, store_remapped=args.store_remapped)
    metrics = read(folder / 'runs' / output / 'metrics.json')
    if not np.isclose(metrics['requested_support']['visible_fraction'], measured['visible_support']):
        raise ValueError('Merged coverage differs from the source audits')
    record = read(folder / 'project.json')
    for name in args.runs:
        run = folder / 'runs' / name
        if (run / 'capture-role.json').exists() and read(run / 'capture-role.json').get('role') == 'supplement':
            summary = read(folder / 'view' / name / 'run.json')
            record['runs'] = [r for r in record['runs'] if r['id'] != name]
            record['runs'].append(dict(id=name, title='Supplement: ' + name, points=summary['total_points']))
    record['preferred_run'] = output
    write(folder / 'project.json', record)
    print('COMBINED_REFINEMENT', output, measured['visible_support'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    capture = commands.add_parser('capture')
    capture.add_argument('plan', type=Path); capture.add_argument('--run', required=True)
    capture.add_argument('--start', type=int, default=0); capture.add_argument('--stop', type=int)
    capture.add_argument('--state-dir', type=Path, required=True); capture.add_argument('--dry-run', action='store_true')
    compare = commands.add_parser('compare')
    compare.add_argument('dataset'); compare.add_argument('runs', nargs='+'); compare.add_argument('--output', type=Path, required=True)
    combine = commands.add_parser('combine')
    combine.add_argument('dataset'); combine.add_argument('runs', nargs='+'); combine.add_argument('--output-run', required=True)
    combine.add_argument('--title', default='Additional views for measured coverage gaps')
    combine.add_argument('--store-remapped', action='store_true', help='Write remapped NPZ arrays without compression for faster local combination; uses more disk space.')
    args = parser.parse_args()
    if args.command == 'capture':
        if args.start < 0 or (args.stop is not None and args.stop <= args.start):
            raise ValueError('Invalid position slice')
        capture_batch(args)
    elif args.command == 'compare':
        result = compare_runs(args.dataset, args.runs); write(args.output, result)
        print({k:v for k,v in result.items() if k != 'objects'}, flush=True)
    else:
        combine_runs(args)


if __name__ == '__main__':
    main()
