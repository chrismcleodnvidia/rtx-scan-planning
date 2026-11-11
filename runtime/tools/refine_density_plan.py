"""Refine a density plan, then check it on a fresh, larger sample set.

The earlier check becomes planning evidence in this round. The published
100000-point post-capture audit is not used to choose scanner positions.
"""
from pathlib import Path
import argparse,json,sys,time
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import read,write,digest,project
from geometry_ops import Geometry
from density_plan import BeamGrid

parser=argparse.ArgumentParser();parser.add_argument('previous',type=Path);parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--seed',type=int,default=2026110829);parser.add_argument('--samples',type=int,default=240000)
parser.add_argument('--already-supported-guid',help='Keep all existing positions for this group; prioritise additional positions elsewhere only if this group already predicts at least99% support.')
parser.add_argument('--group-target',type=float,default=1.)
parser.add_argument('--minimum-group-support',type=float,default=.99,help='Planning support for sampled IFC classes and elements with at least 30 visible samples.')
parser.add_argument('--candidates',type=Path,help='Optional expanded candidate list; source candidate file remains unchanged.')
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
if not .99<=args.group_target<=1:raise ValueError('Group planning target must be between99% and100%')
if not .99<=args.minimum_group_support<=1:raise ValueError('Minimum group support must be between99% and100%')
plan=read(args.previous/'plan.json');folder=project(plan['dataset']);base=folder/'runs'/plan['base_run']
capture=read(base/'capture.json');beam=BeamGrid(capture);g=Geometry(folder/'geometry/mesh.npz')
source=np.load(args.previous/'independent-planning-check.npz');prior=np.load(args.previous/'independent-predictions.npz')
focus=prior['visible'].copy()
if args.already_supported_guid:
    labels=read(folder/'geometry/labels.json');owner=next(x['index'] for x in labels if x['guid']==args.already_supported_guid)
    group=focus&(source['label_index']==owner)
    if not group.any() or np.mean(prior['supported'][group])<.99:raise ValueError('The retained group is not sufficiently supported yet')
    focus&=source['label_index']!=owner
missing=focus&~prior['supported'];sample={key:source[key][missing] for key in ['points','normals','label_index']}
needed=max(0,int(np.ceil(args.group_target*focus.sum()))-int(np.sum(focus&prior['supported'])))
candidate_path=args.candidates or folder/'station-candidates.json'
candidates=read(candidate_path)['stations'];occupied={tuple(np.asarray(s['position_m'],dtype='float32')) for s in capture['stations']+plan['stations']}
pool=[s for s in candidates if tuple(np.asarray(s['position_m'],dtype='float32')) not in occupied]
progress=dict(status='refining',previous=str(args.previous),remaining_planning_samples=int(missing.sum()),candidate_count=len(pool),started=time.time())
write(args.output/'progress.json',progress)
masks=np.zeros((len(pool),int(missing.sum())),bool)
for i,station in enumerate(pool):
    _,masks[i]=beam.support(g,station['position_m'],sample,.018)
    if i%500==0:print('REFINEMENT_CANDIDATES',i+1,len(pool),flush=True)
covered=np.zeros(len(sample['points']),bool);available=np.ones(len(pool),bool);scores=masks.sum(1,dtype=np.int64);chosen=[]
while available.any() and int(covered.sum())<needed:
    best=int(np.argmax(np.where(available,scores,-1)))
    if scores[best]<=0:break
    newly=masks[best]&~covered;covered|=masks[best];available[best]=False;chosen.append(best)
    scores-=np.sum(masks[:,newly],axis=1,dtype=np.int64)
# Overall area can hide gaps on smaller element classes. Retain the original
# per-element planning intent and also check classes of many small components.
labels=read(folder/'geometry/labels.json')
class_names,class_for_owner=np.unique([row['ifc_class'] for row in labels],return_inverse=True)
full_owners=source['label_index'];missing_owners=sample['label_index'];groups=[]
for kind,full_values,missing_values,names in [
        ('element',full_owners,missing_owners,[row['guid'] for row in labels]),
        ('class',class_for_owner[full_owners],class_for_owner[missing_owners],class_names)]:
    counts=np.bincount(full_values[focus],minlength=len(names))
    before=np.bincount(full_values[focus&prior['supported']],minlength=len(names))
    for index in np.flatnonzero(counts>=30):
        groups.append(dict(kind=kind,name=str(names[index]),count=int(counts[index]),
            before=int(before[index]),indices=np.flatnonzero(missing_values==index)))
while available.any():
    deficit=np.zeros(len(covered),bool)
    for group in groups:
        indices=group['indices']
        if group['before']+int(covered[indices].sum())<args.minimum_group_support*group['count']:
            deficit[indices[~covered[indices]]]=True
    if not deficit.any():break
    gains=masks[:,deficit].sum(axis=1,dtype=np.int64);gains[~available]=-1
    best=int(np.argmax(gains))
    if gains[best]<=0:break
    covered|=masks[best];available[best]=False;chosen.append(best)
unmet_groups=[dict(kind=g['kind'],name=g['name'],visible_samples=g['count'],
    predicted_support=(g['before']+int(covered[g['indices']].sum()))/g['count'])
    for g in groups if g['before']+int(covered[g['indices']].sum())<args.minimum_group_support*g['count']]
result=dict(plan);result['stations']=list(plan['stations'])
result['candidate_count']=len(candidates)
for index in chosen:
    station=dict(pool[index]);station['candidate_id']=station['id'];station['id']=f'D{len(result["stations"])+1:04}'
    station['selection_reason']='Additional position for point-density gaps found in the earlier separate planning check'
    result['stations'].append(station)
result['refinement_round']=dict(previous_plan=str(args.previous/'plan.json'),previous_plan_sha256=digest(args.previous/'plan.json'),
    previous_check_becomes_planning=True,previous_check_samples=len(prior['visible']),previous_check_unsupported=int(missing.sum()),
    additional_positions=len(chosen),remaining_unsupported=int(np.sum(~covered)),predictor_sha256=digest(ROOT/'density_plan.py'),
    retained_already_supported_guid=args.already_supported_guid,focused_group_target=args.group_target,
    minimum_group_support=args.minimum_group_support,remaining_planning_group_gaps=unmet_groups,
    candidate_file=str(candidate_path),candidate_file_sha256=digest(candidate_path),
    focus_note='All earlier positions and all source surfaces are retained. Priority affects only selection of additional positions; the fresh check still evaluates the whole asset.')
result.pop('independent_planning_check',None)
write(args.output/'plan.json',result)
progress.update(status='checking_fresh_samples',selected_positions=len(result['stations']),additional_positions=len(chosen));write(args.output/'progress.json',progress)
v=g.data['vertices'];faces=g.data['faces'];tri=v[faces];cross=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);area=np.linalg.norm(cross,axis=1)/2
rng=np.random.default_rng(args.seed);n=args.samples;pick=rng.choice(len(faces),n,p=area/area.sum());uv=rng.random((n,2));u=np.sqrt(uv[:,0]);t=tri[pick]
points=((1-u[:,None])*t[:,0]+(u*(1-uv[:,1]))[:,None]*t[:,1]+(u*uv[:,1])[:,None]*t[:,2]).astype('float32')
check=dict(points=points,normals=cross[pick]/np.maximum(area[pick,None]*2,1e-20),label_index=g.data['face_labels'][pick])
np.savez_compressed(args.output/'independent-planning-check.npz',**check)
del tri,cross,area,t
visible=np.zeros(n,bool);supported=visible.copy()
for i,station in enumerate(capture['stations']+result['stations']):
    vis,sup=beam.support(g,station['position_m'],check,.02);visible|=vis;supported|=sup
    if i%50==0:print('FRESH_DENSITY_CHECK',i+1,len(capture['stations'])+len(result['stations']),flush=True)
np.savez_compressed(args.output/'independent-predictions.npz',visible=visible,supported=supported)
result['independent_planning_check']=dict(seed=args.seed,samples=n,visible_samples=int(visible.sum()),predicted_support_20mm=float(np.mean(supported[visible])),scope='Fresh uncaptured prediction; retained original capture-audit samples unchanged.')
write(args.output/'plan.json',result);progress.update(status='complete',finished=time.time(),independent_check=result['independent_planning_check']);write(args.output/'progress.json',progress)
print('REFINED_DENSITY_PLAN_READY',json.dumps({k:v for k,v in result.items() if k!='stations'}),flush=True)
