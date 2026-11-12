"""Export exact native hits for download and bounded GPU previews for the viewer."""
from core import bim_metadata,scene_path
from pathlib import Path
import shutil,numpy as np
from scipy.spatial import cKDTree
from core import read,write,digest,PROFILES
def sample_indices_by_guid(values):
 """Index audit samples once instead of searching every sample for every mesh."""
 groups={}
 for index,value in enumerate(values):groups.setdefault(str(value),[]).append(index)
 return {guid:np.asarray(indices,dtype=np.intp) for guid,indices in groups.items()}

def package_run(folder,run,measure=True,publish=True):
 folder=Path(folder);run=Path(run);capture=read(run/'capture.json');meta=bim_metadata(folder);labels=read(run/'labels.json')
 if capture.get('validation_status')!='passed_capture_checks':raise ValueError('Native capture did not pass its checks')
 if capture['source_revision']!=meta['source_ifc_sha256']:raise ValueError('Scan/model revision mismatch')
 for label in labels:
  if label['ifc_guid'] not in meta['elements']:raise ValueError('Unresolved IFC identity')
 view=folder/'view'/run.name;view.mkdir(exist_ok=True);write(view/'labels.json',labels);summary=dict(id=run.name,title=capture.get('title',run.name),source_revision=capture['source_revision'],stations=[],total_points=0,preview_points=0,source_type='native RTX',limitations=capture['limitations'])
 if measure:
  from geometry_ops import Geometry
  from planner import visibility
  g=Geometry(folder/'geometry/mesh.npz');s=np.load(folder/'geometry/audit.npz');gl=read(folder/'geometry/labels.json');guids=np.array([x['guid'] for x in gl]);sample_guid=guids[s['label_index']];nearest=np.full(len(s['points']),np.inf);visible=np.zeros(len(nearest),bool);total=sum(x['points'] for x in capture['stations']);distance=np.lib.format.open_memmap(run/'surface-errors.npy',mode='w+',dtype='float32',shape=(total,));first=np.lib.format.open_memmap(run/'first-hit-errors.npy',mode='w+',dtype='float64',shape=(total,));offset=0;sum_error=sum_squared=0.;over_1cm=0;cfg=dict(near=capture['sensor_config']['nearRangeM'][1],far=capture['sensor_config']['farRangeM'][1],elevation=capture['elevation_degrees'])
  audit_by_guid=sample_indices_by_guid(sample_guid)
  first_query=dict(compared_returns=0,no_hit_queries=0,differences_over_1cm=0,max_difference_m=0.,no_hit_sentinel_m=100.,scope='Independent ray re-query. A miss is counted separately; the 100 m no-hit sentinel is not a measured surface distance. Edge-sensitive differences are retained.')
 for si,station in enumerate(capture['stations']):
  d=np.load(run/station['file']);p=d['points'];li=d['label_index'];n=len(p);rng=np.random.default_rng(1000+si);ix=np.sort(rng.choice(n,min(n,45000),replace=False));np.c_[p[ix],li[ix]].astype('<f4').tofile(view/(station['id']+'.bin'))
  summary['stations'].append(dict(id=station['id'],position_m=station['position_m'],points=n,preview_points=len(ix),file=station['id']+'.bin'));summary['total_points']+=n;summary['preview_points']+=len(ix)
  ply=run/(station['id']+'.ply')
  if not ply.exists():
   array=np.empty(n,dtype=[('x','<f4'),('y','<f4'),('z','<f4'),('intensity','<f4'),('ifc_label','<u4')]);array['x'],array['y'],array['z']=p.T;array['intensity']=d['intensity'];array['ifc_label']=li
   with ply.open('wb') as f:f.write(f'ply\nformat binary_little_endian 1.0\ncomment ifc_label indexes labels.json; source_revision {capture["source_revision"]}\nelement vertex {n}\nproperty float x\nproperty float y\nproperty float z\nproperty float intensity\nproperty uint ifc_label\nend_header\n'.encode());array.tofile(f)
  if measure:
   for start in range(0,n,500000):
    chunk=p[start:start+500000];err=g.nearest(chunk)[0];hit,hit_face=g.rays(station['position_m'],chunk);fe=np.abs(hit-np.linalg.norm(chunk-np.array(station['position_m']),axis=1));distance[offset:offset+len(err)]=err;first[offset:offset+len(err)]=fe;offset+=len(err);sum_error+=float(err.sum(dtype='float64'));sum_squared+=float(np.sum(err.astype('float64')**2));over_1cm+=int(np.sum(fe>.01))
    valid=hit_face>=0;first_query['compared_returns']+=int(valid.sum());first_query['no_hit_queries']+=int((~valid).sum());first_query['differences_over_1cm']+=int(np.sum(fe[valid]>.01));first_query['max_difference_m']=max(first_query['max_difference_m'],float(fe[valid].max()) if valid.any() else 0.)
   visible|=visibility(g,np.array(station['position_m']),s,cfg)
   order=np.argsort(li,kind='stable');sorted_labels=li[order]
   unique_labels,starts=np.unique(sorted_labels,return_index=True)
   ends=np.r_[starts[1:],len(order)]
   for index,start,end in zip(unique_labels,starts,ends):
    match=audit_by_guid.get(labels[int(index)]['ifc_guid'],())
    if len(match):nearest[match]=np.minimum(nearest[match],cKDTree(p[order[start:end]]).query(s['points'][match],workers=4)[0])
  print('PACKAGED',run.name,station['id'],n,flush=True)
 if measure:
  assert offset==total;distance.flush();first.flush();thresholds=[.001,.005,.01,.02,.05,.1];by_object=[]
  for label in gl:
   mask=(s['label_index']==label['index'])&visible
   by_object.append(dict(guid=label['guid'],name=label['name'],samples=int(mask.sum()),visible_support_2cm=float(np.mean(nearest[mask]<=.02)) if mask.any() else None))
  metrics=dict(points_evaluated=len(distance),surface_error=dict(mae_m=sum_error/total,rmse_m=float(np.sqrt(sum_squared/total)),p95_m=float(np.quantile(distance,.95,overwrite_input=True)),max_m=float(distance.max())),visible_area_m2=float(visible.sum()*s['sample_area_m2']),thresholds=[dict(tolerance_m=t,visible_fraction=float(np.mean(nearest[visible]<=t)) if visible.any() else 0.,all_surface_fraction=float(np.mean(nearest<=t))) for t in thresholds],objects=by_object,ifc_linked_fraction=1.0,audit_samples=len(nearest),accuracy_scope='Agreement with input geometry; not calibrated physical scanner accuracy',coverage_scope='Independent area samples; same IFC identity required; opaque visibility')
  metrics['first_hit_error']=dict(p95_m=float(np.quantile(first,.95,overwrite_input=True)),max_m=float(first.max()),over_1cm=over_1cm,scope='Legacy raw statistic includes the 100 m no-hit sentinel. Use first_hit_query for valid comparisons and separate misses. Edge-sensitive discrepancies are retained.')
  metrics['first_hit_query']=first_query
  settings=read(run/'stations.json').get('settings',{});support_target=settings.get('target_sample_support',.99);tolerance=settings.get('coverage_tolerance_m',.02);metrics['requested_support']=dict(tolerance_m=tolerance,target=support_target,visible_fraction=float(np.mean(nearest[visible]<=tolerance)) if visible.any() else 0.);metrics['visible_support_2cm_target']=.99;metrics['visible_support_2cm_target_met']=bool(np.mean(nearest[visible]<=.02)>=.99) if visible.any() else False
  np.savez_compressed(run/'coverage-audit.npz',nearest=nearest,visible=visible);write(run/'metrics.json',metrics);np.c_[s['points'],np.where(~visible,0,np.where(nearest<=.02,2,1))].astype('<f4').tofile(view/'coverage.bin');summary['metrics']=metrics
 elif (run/'evaluation.json').exists():
  old=read(run/'evaluation.json');c=old['coverage'][-1];summary['metrics']=dict(surface_error=old['surface_error'],visible_area_m2=c['visible_reference_area_m2'],thresholds=[dict(tolerance_m=x['tolerance_m'],visible_fraction=x['visible_surface_fraction']) for x in c['thresholds']],ifc_linked_fraction=old['ifc_linked_fraction'],accuracy_scope='Prior validated run; geometry agreement only',objects=[])
 summary['download']=dict(labels='labels.json',metadata='ifc-metadata.json',format='Binary PLY: x/y/z/intensity/ifc_label + label-index table; native properties in building.usdc; ifc-metadata.json is a derived convenience copy');write(view/'run.json',summary)
 if publish:
  project=read(folder/'project.json');project['runs']=[x for x in project.get('runs',[]) if x['id']!=run.name]+[dict(id=run.name,title=summary['title'],points=summary['total_points'])];write(folder/'project.json',project)
 if measure:
  from survey import build_report
  build_report(folder,run.name)
 return summary
