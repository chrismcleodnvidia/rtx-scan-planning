"""Generate station candidates from IFC floor surfaces, then optimize visibility."""
from core import bim_metadata,scene_path
from pathlib import Path
import re
import numpy as np
from core import read,write,PROFILES,digest
from geometry_ops import Geometry
def visibility(g,origin,sample,profile):
 p=sample['points'];delta=p-origin;distance=np.linalg.norm(delta,axis=1);directions=delta/np.maximum(distance[:,None],1e-9);hit,_=g.rays(origin,p);el=np.rad2deg(np.arcsin(np.clip(directions[:,2],-1,1)));incidence=np.abs(np.sum(directions*sample['normals'],axis=1))
 return (abs(hit-distance)<.002)&(distance>=profile['near'])&(distance<=min(profile['far'],99.))&(el>=profile['elevation'][0])&(el<=profile['elevation'][1])&(incidence>=np.cos(np.deg2rad(80)))
def plan(folder,profile='dense',spacing=3.,target=.99,manual=None,all_candidates=False,exterior=False,elevated=False):
 from survey import ensure_brief,brief_hash,required_indices,element_visibility,build_report
 folder=Path(folder);brief=ensure_brief(folder);cfg=PROFILES[profile];g=Geometry(folder/'geometry/mesh.npz');labels=read(folder/'geometry/labels.json');metadata=bim_metadata(folder);s=np.load(folder/'geometry/planning.npz');geo=g.data;v=geo['vertices'];f=geo['faces'];fl=geo['face_labels'];tri=v[f];cross=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);area=np.linalg.norm(cross,axis=1)/2
 floors=[]
 for label in labels:
  item=metadata['elements'][label['guid']];kind=str(item['attributes'].get('PredefinedType','')).upper();name=(item['name'] or '').lower()
  if (label['ifc_class']=='IfcSlab' and kind!='ROOF' and 'roof' not in name) or (label['ifc_class']=='IfcCovering' and kind=='FLOORING'):floors.append(label['index'])
 # A reviewed dataset selection can identify floors exported as building parts.
 # This is a station-planning hint, not a change to the IFC class or CAD geometry.
 floor_selection=read(folder/'scan-floor-selection.json') if (folder/'scan-floor-selection.json').exists() else None
 if floor_selection:
  if floor_selection['source_revision']!=metadata['source_ifc_sha256']:raise ValueError('Floor selection belongs to another source revision')
  selected_floors=set(floor_selection['guids'])
  if selected_floors-set(metadata['elements']):raise ValueError('Unknown floor-selection identity')
  floors=sorted(set(floors)|{l['index'] for l in labels if l['guid'] in selected_floors})
 candidates=[];rejected=0;seen=set()
 if manual is not None:
  if not manual:raise ValueError('Provide at least one station')
  ids=[s.get('id','') for s in manual]
  if len(set(ids))!=len(ids) or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',x) for x in ids):raise ValueError('Station IDs must be unique letters, numbers, underscores or hyphens')
  for station in manual:
   p=np.array(station['position_m'],dtype='float32')
   if p.shape!=(3,) or not np.isfinite(p).all():raise ValueError('Station requires three finite metre coordinates')
   clearance=float(g.nearest(p[None])[0][0])
   if clearance<.15:raise ValueError('Manual station is too close to geometry: '+station['id'])
   candidates.append(dict(id=station['id'],position_m=p.tolist(),clearance_m=clearance,group='manual synthetic station'))
 else:
  if not floors and not exterior and not elevated:raise ValueError('No IFC floor slabs or flooring coverings found. Supply a reviewed station JSON with the CLI --stations option.')
  mask=np.isin(fl,floors)&(area>1e-8)&(abs(cross[:,2])/np.maximum(2*area,1e-12)>.995);ft=tri[mask];fa=area[mask];heights=np.round(ft[:,:,2].mean(1)/.05)*.05;levels=[]
  for h in np.unique(heights):
   matching=abs(heights-h)<.001
   if fa[matching].sum()>=2:levels.append((h,ft[matching].reshape(-1,3).min(0),ft[matching].reshape(-1,3).max(0)))
  for h,lo,hi in levels:
   for x in np.arange(lo[0]+spacing/2,hi[0],spacing):
    for y in np.arange(lo[1]+spacing/2,hi[1],spacing):
     p=np.array([x,y,h+1.6],dtype='float32');down,face=g.rays(p,p[None]+[0,0,-1])
     if face[0]<0 or fl[face[0]] not in floors or abs(float(down[0])-1.6)>.08:rejected+=1;continue
     clearance=float(g.nearest(p[None])[0][0])
     if clearance<.35:rejected+=1;continue
     key=tuple(np.round(p/.2).astype(int))
     if key in seen:continue
     seen.add(key)
     candidates.append(dict(id=f'S{len(candidates)+1:03}',position_m=p.tolist(),clearance_m=clearance,floor_z_m=float(p[2]-down[0]),group='floor-supported synthetic station'))
 # Stairs need their own positions: a floor grid can miss treads and landings.
 if manual is None:
  stair_guids=set((floor_selection or {}).get('stair_guids',[]))
  stair_indices=[l['index'] for l in labels if l['ifc_class'] in ('IfcStairFlight','IfcStair') or l['guid'] in stair_guids or (l['ifc_class']=='IfcSlab' and str(metadata['elements'][l['guid']]['attributes'].get('PredefinedType','')).upper()=='LANDING')]
  stair_mask=np.isin(fl,stair_indices)&(area>.02)&(abs(cross[:,2])/np.maximum(2*area,1e-12)>.99)
  seeds=tri[stair_mask].mean(1);seed_labels=fl[stair_mask];seed_seen=set()
  for ground,owner in zip(seeds,seed_labels):
   cell=tuple(np.round(ground/.4).astype(int))
   if cell in seed_seen:continue
   seed_seen.add(cell)
   for height in [.8,1.2,1.6]:
    p=np.asarray(ground+[0,0,height],dtype='float32');down,face=g.rays(p,p[None]+[0,0,-1]);key=tuple(np.round(p/.2).astype(int))
    if key in seen or face[0]<0 or fl[face[0]]!=owner or abs(float(down[0])-height)>.08:continue
    clearance=float(g.nearest(p[None])[0][0])
    if clearance<.25:continue
    seen.add(key);candidates.append(dict(id=f'S{len(candidates)+1:03}',position_m=p.tolist(),clearance_m=clearance,floor_z_m=float(ground[2]),scanner_height_m=height,group='stair-focused synthetic station; tripod fit/access unverified'))
 if manual is None and (exterior or elevated):
  lo=v.min(0);hi=v.max(0);offset=3.;proposals=[]
  if exterior:
   for x in np.arange(lo[0]-offset,hi[0]+offset+spacing/2,spacing):
    for y in [lo[1]-offset,hi[1]+offset]:proposals.append(([x,y,lo[2]+1.6],'exterior synthetic station; ground/access unverified'))
   for y in np.arange(lo[1],hi[1]+spacing/2,spacing):
    for x in [lo[0]-offset,hi[0]+offset]:proposals.append(([x,y,lo[2]+1.6],'exterior synthetic station; ground/access unverified'))
  if elevated:
   for x in np.arange(lo[0],hi[0]+spacing/2,spacing):
    for y in np.arange(lo[1],hi[1]+spacing/2,spacing):proposals.append(([x,y,hi[2]+offset],'elevated synthetic station; not a field access plan'))
  for pos,group in proposals:
   p=np.array(pos,dtype='float32');clearance=float(g.nearest(p[None])[0][0]);key=tuple(np.round(p/.2).astype(int))
   if clearance<.35 or key in seen:continue
   seen.add(key);candidates.append(dict(id=f'S{len(candidates)+1:03}',position_m=p.tolist(),clearance_m=clearance,group=group))
 if not candidates:raise ValueError('No clear floor-supported stations. Reduce candidate spacing or supply manual stations.')
 write(folder/'station-candidates.json',dict(source_revision=metadata['source_ifc_sha256'],stations=candidates))
 masks=np.lib.format.open_memmap(folder/'geometry/candidate-visibility.npy',mode='w+',dtype='bool',shape=(len(candidates),len(s['points'])))
 for i,c in enumerate(candidates):
  masks[i]=visibility(g,np.array(c['position_m']),s,cfg)
  if i%20==0:print('CANDIDATE_VISIBILITY',i+1,len(candidates),flush=True)
 masks.flush();reachable=masks.any(0);covered=np.zeros(len(reachable),bool);counts=np.zeros(len(reachable),int);selected=[];remaining=set(range(len(candidates)))
 while remaining and not (manual is not None or all_candidates):
  best=max(remaining,key=lambda j:float(np.sum(masks[j]&~covered))+.12*float(np.sum(masks[j]&(counts==1))));gain=int(np.sum(masks[best]&~covered));selected.append(dict(candidates[best],new_visible_area_m2=float(gain*s['sample_area_m2'])));covered|=masks[best];counts+=masks[best];remaining.remove(best)
  if covered.sum()/max(1,reachable.sum())>=target:break
 if manual is not None or all_candidates:selected=candidates;covered=masks.any(0);counts=masks.sum(0)
 # After the area-weighted pass, fill per-element and repeat-view deficits.
 # The target is bounded by what these candidates can see; unseen objects stay in the report.
 required=required_indices(labels,brief)
 if manual is None and not all_candidates:
  attainable=np.minimum(np.asarray(masks.sum(0)),brief['minimum_views']);desired=np.zeros(len(reachable),bool)
  for index in required:desired|=s['label_index']==index
  while remaining:
   weights=np.zeros(len(reachable),dtype='float32')
   for index in required:
    mask=(s['label_index']==index)&reachable
    if not mask.any():continue
    if np.mean(counts[mask]>=attainable[mask])<brief['element_support_target']:
     deficit=mask&(counts<attainable);weights[deficit]=1/max(1,int(mask.sum()))
   if not weights.any():break
   best=max(remaining,key=lambda j:float(np.sum(masks[j]*weights)))
   if not np.any(masks[best]&(weights>0)):break
   gain=int(np.sum(masks[best]&~covered));selected.append(dict(candidates[best],new_visible_area_m2=float(gain*s['sample_area_m2']),selection_reason='per-element visibility / repeat-view gap'));covered|=masks[best];counts+=masks[best];remaining.remove(best)
 element_report=element_visibility(labels,s['label_index'],reachable,covered,counts,brief)
 plan=dict(survey_brief_sha256=brief_hash(brief),element_visibility=element_report,source_revision=metadata['source_ifc_sha256'],scene_sha256=digest(scene_path(folder)),profile=profile,settings=dict(spacing_m=spacing,scan_count_limit=None,use_all_candidates=all_candidates,target_visible_fraction=target,scanner_height_m=1.6,minimum_clearance_m=.35,coverage_tolerance_m=brief['support_tolerance_mm']/1000,target_sample_support=brief['element_support_target'],exterior_candidates=exterior,elevated_candidates=elevated),stations=selected,candidate_count=len(candidates),rejected_candidates=rejected,candidate_limit_reached=False,candidate_reachable_area_m2=float(reachable.sum()*s['sample_area_m2']),selected_visible_area_m2=float(covered.sum()*s['sample_area_m2']),reachable_fraction_seen=float(covered.sum()/max(1,reachable.sum())),reachable_fraction_seen_twice=float(np.sum(counts>=2)/max(1,reachable.sum())),all_reference_area_m2=float(len(reachable)*s['sample_area_m2']),target_met=bool(covered.sum()/max(1,reachable.sum())>=target),assumptions=['Visibility relative to generated candidates, not all physically possible scanner positions','Opaque triangle visibility; incidence <=80 degrees, profile range/FOV','Floor clearance is checked; human access and route safety are not certified','Targets are editable experiment criteria, not an industry standard','Planning samples and post-scan audit samples use different random seeds'])
 write(folder/'plan.json',plan);np.savez_compressed(folder/'geometry/planning-visibility.npz',covered=covered,reachable=reachable,observations=counts);np.c_[s['points'],covered.astype('float32')+reachable.astype('float32')].astype('<f4').tofile(folder/'view/coverage-plan.bin');build_report(folder);print('PLAN_READY',len(selected),plan['reachable_fraction_seen'],flush=True);return plan
