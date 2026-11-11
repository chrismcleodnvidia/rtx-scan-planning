"""Add clear-air viewpoints near building patches with predicted density gaps."""
from pathlib import Path
import argparse,sys
import numpy as np
from scipy.spatial import cKDTree
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import read,write,project
from geometry_ops import Geometry

parser=argparse.ArgumentParser();parser.add_argument('plan_directory',type=Path);parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--already-supported-guid')
parser.add_argument('--distances',type=float,nargs='+',default=[1.5,3.],help='Normal offsets from a surface patch, in metres; all positions still require clear air.')
args=parser.parse_args()
if any(distance<=.35 for distance in args.distances):raise ValueError('Surface offsets must exceed the 0.35 m clearance criterion')
plan=read(args.plan_directory/'plan.json');folder=project(plan['dataset'])
labels=read(folder/'geometry/labels.json');data=np.load(args.plan_directory/'independent-planning-check.npz');prediction=np.load(args.plan_directory/'independent-predictions.npz')
missing=prediction['visible']&~prediction['supported']
if args.already_supported_guid:
    owner=next(x['index'] for x in labels if x['guid']==args.already_supported_guid)
    group=prediction['visible']&(data['label_index']==owner)
    if np.mean(prediction['supported'][group])<.99:raise ValueError('Ground planning support is not yet sufficient')
    missing&=data['label_index']!=owner
patches={}
for point,normal,owner in zip(data['points'][missing],data['normals'][missing],data['label_index'][missing]):
    key=(int(owner),*np.floor(point/3).astype(int),*np.rint(normal*2).astype(int))
    patches.setdefault(key,[]).append((point,normal))
g=Geometry(folder/'geometry/mesh.npz');original=read(folder/'station-candidates.json')
anchors=np.asarray([s['position_m'] for s in original['stations']],dtype='float32');tree=cKDTree(anchors)
lowest=float(g.data['vertices'][:,2].min())+.35
proposals=[];seen=set()
for key,group in patches.items():
    center=np.mean([p for p,n in group],axis=0);normal=np.mean([n for p,n in group],axis=0)
    normal/=max(float(np.linalg.norm(normal)),1e-12)
    axis=np.eye(3)[int(np.argmin(np.abs(normal)))];tangent=np.cross(normal,axis);tangent/=max(float(np.linalg.norm(tangent)),1e-12);other=np.cross(normal,tangent)
    for sign in [-1,1]:
        for distance in args.distances:
            for tangent_direction in [np.zeros(3),tangent,-tangent,other,-other]:
                point=(center+normal*sign*distance+tangent_direction*distance*.65).astype('float32')
                cell=tuple(np.rint(point/.2).astype(int))
                if point[2]<lowest or cell in seen:continue
                seen.add(cell);proposals.append((point,key[0]))
positions=np.asarray([p for p,owner in proposals]);clearance=g.nearest(positions)[0]
kept=[];rejected_clearance=0;rejected_connection=0
for i,((point,owner),gap) in enumerate(zip(proposals,clearance)):
    if gap<.35:rejected_clearance+=1;continue
    distances,indices=tree.query(point,k=min(8,len(anchors)))
    targets=anchors[np.atleast_1d(indices)];distances=np.atleast_1d(distances)
    hit,face=g.rays(point,targets)
    # A clear segment to an existing supported/elevated candidate prevents
    # accepting an arbitrary position enclosed inside solid material.
    connected=(distances<80)&((face<0)|(hit>distances-.02))
    if not connected.any():rejected_connection+=1;continue
    kept.append(dict(id=f'P{len(kept)+1:05}',position_m=point.tolist(),clearance_m=float(gap),
        group='near-surface synthetic viewpoint at the height of the density gap',
        target_ifc_guid=labels[owner]['guid'],clear_air_anchor=original['stations'][int(np.atleast_1d(indices)[np.flatnonzero(connected)[0]])]['id']))
    if i%500==0:print('SURFACE_VIEWPOINTS',i,len(proposals),len(kept),flush=True)
write(args.output,dict(source_revision=plan['source_revision'],stations=original['stations']+kept,
    added_positions=len(kept),patches=len(patches),proposals=len(proposals),rejected_clearance=rejected_clearance,
    surface_offsets_m=args.distances,
    rejected_clear_air_connection=rejected_connection,
    scope='Additional synthetic scanner poses only; no source geometry or source-element exclusions. Existing ground scan positions are retained.'))
print('SURFACE_CANDIDATES_READY',len(kept),'added;',len(original['stations'])+len(kept),'total',flush=True)
