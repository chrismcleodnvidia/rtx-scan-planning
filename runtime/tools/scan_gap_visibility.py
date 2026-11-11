"""Check first-hit IFC identity at unsupported scan-audit locations.

This explains a coverage result; it does not change the audit or exclude surfaces.
"""
from pathlib import Path
import argparse, sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import read, write, project
from density_plan import BeamGrid
from geometry_ops import Geometry
from scan_refinement import compare_runs


def diagnose(dataset, runs):
    measured = compare_runs(dataset, runs)
    folder = project(dataset)
    captures = [read(folder / 'runs' / name / 'capture.json') for name in runs]
    audits = [np.load(folder / 'runs' / name / 'coverage-audit.npz') for name in runs]
    sample = np.load(folder / 'geometry/audit.npz')
    visible = np.logical_or.reduce([a['visible'] for a in audits])
    nearest = np.minimum.reduce([a['nearest'] for a in audits])
    missing = visible & (nearest > measured['tolerance_m'])
    points = sample['points'][missing]; normals = sample['normals'][missing]
    owners = sample['label_index'][missing]
    beam = BeamGrid(captures[0]); g = Geometry(folder / 'geometry/mesh.npz')
    labels = read(folder / 'geometry/labels.json')
    own = np.zeros(len(points), bool); other = own.copy()
    for capture in captures:
        for station in capture['stations']:
            origin = np.asarray(station['position_m']); delta = points - origin
            distance = np.linalg.norm(delta, axis=1)
            elevation = np.arctan2(delta[:, 2], np.linalg.norm(delta[:, :2], axis=1))
            incidence = np.abs(np.sum(delta / np.maximum(distance[:, None], 1e-12) * normals, axis=1))
            indices = np.flatnonzero((distance >= beam.near) & (distance <= beam.far) &
                (elevation >= beam.elevation[0]) & (elevation <= beam.elevation[-1]) &
                (incidence >= np.cos(np.radians(80))))
            if not len(indices):
                continue
            hit, face = g.rays(origin, points[indices])
            valid = (face >= 0) & (np.abs(hit - distance[indices]) < .002)
            indices = indices[valid]; face = face[valid]
            same = g.data['face_labels'][face] == owners[indices]
            own[indices[same]] = True; other[indices[~same]] = True
    rows = []
    classes = np.asarray([label['ifc_class'] for label in labels])[owners]
    for name in np.unique(classes):
        mask = classes == name
        rows.append(dict(ifc_class=str(name), unsupported_samples=int(mask.sum()),
            own_element_visible=int((mask & own).sum()),
            only_other_element_within_2mm=int((mask & other & ~own).sum()),
            no_repeat_visibility=int((mask & ~other & ~own).sum())))
    return dict(dataset=dataset, runs=runs, audit_sha256=measured['audit_sha256'],
        visible_support=measured['visible_support'], tolerance_m=measured['tolerance_m'],
        unsupported_samples=int(missing.sum()), classes=rows,
        scope='Re-query of unsupported audit locations from recorded positions. No measurement or source changes.',
        interpretation='Own-element visibility leaves density or first-hit edge behaviour to investigate. Other-element-only hits within the 2 mm visibility tolerance can indicate nearly coincident or occluding surfaces; this does not establish a source defect or prove that no new viewpoint can observe the target.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset'); parser.add_argument('runs', nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(args.dataset, args.runs); write(args.output, result)
    print('SCAN_GAP_VISIBILITY', result['unsupported_samples'], result['classes'], flush=True)
