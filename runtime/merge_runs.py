"""Combine same-source native captures, remapping labels and retaining provenance."""
from pathlib import Path
import argparse,copy,os,shutil
import numpy as np
from core import read,write,digest,project,scene_path
from results import package_run

def merge_first_hit_queries(metrics):
 queries=[m.get('first_hit_query') for m in metrics]
 if not all(queries):return None
 for m,q in zip(metrics,queries):
  if q['compared_returns']+q['no_hit_queries']!=m['points_evaluated']:
   raise ValueError('First-hit query counts do not match capture point count')
 if len({q['no_hit_sentinel_m'] for q in queries})!=1:
  raise ValueError('Incompatible first-hit query miss sentinels')
 return dict(compared_returns=sum(q['compared_returns'] for q in queries),no_hit_queries=sum(q['no_hit_queries'] for q in queries),differences_over_1cm=sum(q['differences_over_1cm'] for q in queries),max_difference_m=max(q['max_difference_m'] for q in queries),no_hit_sentinel_m=queries[0]['no_hit_sentinel_m'],scope='Union of source independent ray queries; query misses are separate from valid first-hit differences. See source captures for distributions.')

def merge(folder,names,output,title,store_remapped=False):
 folder=Path(folder);dest=folder/'runs'/output
 if dest.exists():raise ValueError('Output run already exists')
 runs=[folder/'runs'/name for name in names];records=[read(r/'capture.json') for r in runs];first=records[0]
 for c in records:
  if c['validation_status']!='passed_capture_checks':raise ValueError('Capture not validated')
  for key in ['source_revision','source_scene_sha256','sensor_config','elevation_degrees']:
   if c[key]!=first[key]:raise ValueError('Cannot combine incompatible '+key)
 if first['source_scene_sha256']!=digest(scene_path(folder)):raise ValueError('Source geometry changed')
 for r in runs:
  for required in ['coverage-audit.npz','surface-errors.npy','metrics.json']:
   if not (r/required).exists():raise ValueError('Capture needs retained audit evidence before aggregation: '+str(r/required))
 dest.mkdir();labels=[];by_path={};stations=[];ids=set();sources=[]
 def link(src,dst):
  try:os.link(src,dst)
  except OSError:shutil.copy2(src,dst)
 for run,c in zip(runs,records):
  table=read(run/'labels.json');remap=[]
  for label in table:
   key=(label['prim_path'],label['ifc_guid'])
   if key not in by_path:by_path[key]=len(labels);item=copy.deepcopy(label);item['index']=len(labels);labels.append(item)
   remap.append(by_path[key])
  remap=np.asarray(remap,dtype='int32');identity=np.array_equal(remap,np.arange(len(remap)))
  for station in c['stations']:
   item=copy.deepcopy(station)
   if item['id'] in ids:raise ValueError('Station IDs overlap; combine distinct acquisitions')
   ids.add(item['id']);src=run/item['file'];target=dest/item['file']
   if digest(src)!=item['sha256']:raise ValueError('Capture checksum mismatch')
   if identity:
    link(src,target)
    if (run/(item['id']+'.ply')).exists():link(run/(item['id']+'.ply'),dest/(item['id']+'.ply'))
   else:
    with np.load(src) as d:arrays={k:d[k] for k in d.files}
    arrays['label_index']=remap[arrays['label_index']]
    (np.savez if store_remapped else np.savez_compressed)(target,**arrays);item['sha256']=digest(target)
   item['origin_run']=run.name;stations.append(item)
  sources.append(dict(run=run.name,capture_sha256=digest(run/'capture.json'),metrics_sha256=digest(run/'metrics.json')))
 capture=copy.deepcopy(first);capture.update(title=title,stations=stations,status='complete',elapsed_seconds=sum(c['elapsed_seconds'] for c in records),aggregation=dict(sources=sources,remapped_npz_storage='stored' if store_remapped else 'deflate',method='Concatenated native returns; label indices remapped by USD prim and IFC GlobalId; coordinates unchanged'))
 write(dest/'capture.json',capture);write(dest/'labels.json',labels)
 if (runs[0]/'ifc-metadata.json').exists():link(runs[0]/'ifc-metadata.json',dest/'ifc-metadata.json')
 plan=read(runs[0]/'stations.json');plan['stations']=stations;plan['aggregation']=sources;plan['element_visibility']=[];plan['candidate_count']=len(stations);plan['candidate_reachable_area_m2']=None;plan['selected_visible_area_m2']=None;write(dest/'stations.json',plan);shutil.copy2(runs[0]/'survey-brief.json' if (runs[0]/'survey-brief.json').exists() else folder/'survey-brief.json',dest/'survey-brief.json')
 summary=package_run(folder,dest,measure=False,publish=False)
 finish_metrics(folder,runs,dest,summary,sources)

def finish_metrics(folder,runs,dest,summary,sources):
 output=dest.name
 # Audit arrays are from the same retained independent surface sample file.
 audits=[np.load(r/'coverage-audit.npz') for r in runs];nearest=np.minimum.reduce([a['nearest'] for a in audits]);visible=np.logical_or.reduce([a['visible'] for a in audits]);np.savez_compressed(dest/'coverage-audit.npz',nearest=nearest,visible=visible)
 metrics=[read(r/'metrics.json') for r in runs];total=sum(m['points_evaluated'] for m in metrics);m=copy.deepcopy(metrics[0]);m['points_evaluated']=total;m['retrospective_criteria']=any(not (r/'survey-brief.json').exists() for r in runs)
 combined=np.lib.format.open_memmap(dest/'surface-errors.npy',mode='w+',dtype='float32',shape=(total,));off=0
 for r in runs:
  a=np.load(r/'surface-errors.npy',mmap_mode='r');combined[off:off+len(a)]=a;off+=len(a)
 combined.flush();m['surface_error']={'mae_m':sum(x['surface_error']['mae_m']*x['points_evaluated'] for x in metrics)/total,'rmse_m':float(np.sqrt(sum(x['surface_error']['rmse_m']**2*x['points_evaluated'] for x in metrics)/total)),'max_m':max(x['surface_error']['max_m'] for x in metrics),'p95_m':float(np.quantile(combined,.95,overwrite_input=True))}
 m['first_hit_error']={'over_1cm':sum(x['first_hit_error']['over_1cm'] for x in metrics),'max_m':max(x['first_hit_error']['max_m'] for x in metrics),'scope':'Union of source capture first-hit audits; see source captures for distributions'}
 query=merge_first_hit_queries(metrics)
 if query:m['first_hit_query']=query
 else:m.pop('first_hit_query',None)
 sample=np.load(folder/'geometry/audit.npz');m['visible_area_m2']=float(visible.sum()*sample['sample_area_m2']);m['thresholds']=[dict(tolerance_m=t['tolerance_m'],visible_fraction=float(np.mean(nearest[visible]<=t['tolerance_m'])),all_surface_fraction=float(np.mean(nearest<=t['tolerance_m']))) for t in m['thresholds']]
 m['objects']=[]
 for label in read(folder/'geometry/labels.json'):
  mask=(sample['label_index']==label['index'])&visible;m['objects'].append(dict(guid=label['guid'],name=label['name'],samples=int(mask.sum()),visible_support_2cm=float(np.mean(nearest[mask]<=.02)) if mask.any() else None))
 m.setdefault('requested_support',dict(tolerance_m=.02,target=.99));m['visible_support_2cm_target_met']=bool(np.mean(nearest[visible]<=.02)>=.99);m['requested_support']['visible_fraction']=float(np.mean(nearest[visible]<=m['requested_support']['tolerance_m']));m['aggregation']=dict(sources=sources,audit_sample_sha256=digest(folder/'geometry/audit.npz'))
 write(dest/'metrics.json',m);summary['metrics']=m;write(folder/'view'/output/'run.json',summary)
 np.c_[sample['points'],np.where(~visible,0,np.where(nearest<=.02,2,1))].astype('<f4').tofile(folder/'view'/output/'coverage.bin')
 from survey import build_report
 build_report(folder,output)
 record=read(folder/'project.json');record['runs']=[r for r in record['runs'] if r['id']!=output]+[dict(id=output,title=summary['title'],points=summary['total_points'])];write(folder/'project.json',record)
 print('COMBINED',output,total,'VISIBLE_2CM',m['requested_support']['visible_fraction'],flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('dataset');p.add_argument('output');p.add_argument('runs',nargs='+');p.add_argument('--title',default='Interior + exterior');p.add_argument('--store-remapped',action='store_true');a=p.parse_args()
 if not all(__import__('re').fullmatch('[A-Za-z0-9_-]+',n) for n in [a.output,*a.runs]):raise ValueError('Invalid run identifier')
 merge(project(a.dataset),a.runs,a.output,a.title,store_remapped=a.store_remapped)
