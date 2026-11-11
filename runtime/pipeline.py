"""Standard NVIDIA CAD conversion followed by native RTX scanning."""
from pathlib import Path
import argparse,re,shutil,time,os
from core import bim_metadata,scene_path,ROOT,DATA,PYTHON,ISAAC,PROFILES,read,write,project,digest,command
def import_ifc(source,name=None,native_usd=None):
 """Standard CAD conversion followed by scan-app preparation, with no IFC enrichment."""
 from core import LOCAL
 source=Path(source).resolve();sha=digest(source)
 slug=re.sub(r'[^a-z0-9]+','-',name.lower() if name else source.stem.lower()).strip('-')[:50] or 'building'
 key=slug+'-cad-'+sha[:8];folder=DATA/key
 if (folder/'project.json').exists() and read(folder/'project.json').get('status')=='ready':
  print('DATASET',key,flush=True);return key
 folder.mkdir(parents=True,exist_ok=True);(folder/'model').mkdir(exist_ok=True)
 target=folder/'source.ifc'
 if not target.exists():shutil.copy2(source,target)
 if digest(target)!=sha:raise ValueError('Retained source revision differs')
 write(folder/'project.json',dict(id=key,name=name or source.stem,status='converting',
  metadata_backend='nvidia-cad-native',source_revision=sha,runs=[],created=time.time(),
  description='Standard NVIDIA CAD Converter USD with native IFC metadata; RTX scanning is a downstream consumer.'))
 output=folder/'model/building.usdc'
 if native_usd:
  native_usd=Path(native_usd).resolve();shutil.copy2(native_usd,output)
  receipt=dict(backend='NVIDIA CAD Converter / HOOPS',reused_native_usd=str(native_usd),source_ifc_sha256=sha,usd_sha256=digest(output))
 else:
  cad_python=os.environ.get('BIMSCAN_CAD_PYTHON',LOCAL.get('cad_converter_python',str(PYTHON)))
  args=[cad_python,'-m','usd_convert_cad','--input',target,'--output',output,
   '--convert-metadata','--convert-curves','--up-axis','z','--filter-style','none',
   '--load-hidden','--material-type','preview-surface','--instancing-style','none','--tess-lod','4']
  start=time.perf_counter();command(args,folder/'conversion.log')
  receipt=dict(backend='NVIDIA CAD Converter / HOOPS',command=[str(x) for x in args],
   native_conversion_seconds=time.perf_counter()-start,source_ifc_sha256=sha,usd_sha256=digest(output))
 write(folder/'native-conversion.json',receipt)
 from native_ifc import prepare_scan_scene
 prepare_scan_scene(folder)
 from prepare import prepare
 prepare(folder)
 if digest(output)!=receipt['usd_sha256']:raise ValueError('Scan preparation changed native USD')
 print('DATASET',key,flush=True);return key

def scan(key,profile='dense',resume=None,publish=True):
 from survey import ensure_brief,brief_hash
 folder=project(key);brief=ensure_brief(folder);metadata=bim_metadata(folder)
 if resume:
  if not re.fullmatch(r'[A-Za-z0-9_-]+',resume):raise ValueError('Invalid run identifier')
  run=folder/'runs'/resume;plan=read(run/'stations.json');profile=plan['profile']
 else:
  plan=read(folder/'plan.json')
 if plan['source_revision']!=metadata['source_ifc_sha256'] or plan['scene_sha256']!=digest(scene_path(folder)):raise ValueError('Model changed since planning; regenerate the station plan')
 if plan['profile']!=profile:raise ValueError('Plan the selected scanner profile before capturing')
 if not resume:
  if plan.get('survey_brief_sha256')!=brief_hash(brief):raise ValueError('Survey brief changed or plan predates it; regenerate positions before capture')
  run=folder/'runs'/('rtx-'+profile+'-'+time.strftime('%Y%m%d-%H%M%S'));run.mkdir(parents=True);write(run/'stations.json',plan);write(run/'survey-brief.json',brief)
 cfg=PROFILES[profile]
 done=(run/'capture.json').exists() and read(run/'capture.json').get('validation_status')=='passed_capture_checks'
 if not done:
  cmd=[ISAAC,ROOT/'capture.py','--scene',scene_path(folder),'--stations',run/'stations.json','--output-dir',run,'--vertical-channels',cfg['channels'],'--report-rate',cfg['report_rate'],'--near-range',cfg['near'],'--far-range',cfg['far'],'--elevation-min',cfg['elevation'][0],'--elevation-max',cfg['elevation'][1],'--material-mode',cfg['material_mode'],'--capture-seconds','.2','--min-packets','12']
  if metadata.get('metadata_backend')=='nvidia-cad-native':cmd+=['--native-ifc','--source-revision',metadata['source_ifc_sha256']]
  elif scene_path(folder).name!='building.usdc':cmd+=['--metadata',folder/'model/ifc-metadata.json']
  if resume:cmd+=['--resume']
  write(run/('resume-command.json' if resume else 'command.json'),dict(command=[str(x) for x in cmd],script_sha256=digest(ROOT/'capture.py'),plan_sha256=digest(run/'stations.json')));command(cmd,run/'native.log')
 if not (run/'capture.json').exists() or read(run/'capture.json').get('validation_status')!='passed_capture_checks':raise RuntimeError('RTX capture incomplete; see native.log. Resume this run with --resume '+run.name)
 capture=read(run/'capture.json');capture['title']=cfg['title']+' · '+str(len(plan['stations']))+' stations';write(run/'capture.json',capture)
 from results import package_run
 package_run(folder,run,publish=publish);print('SCAN_COMPLETE',run.name,flush=True);return run.name

def main():
 from filelock import FileLock
 p=argparse.ArgumentParser(description="Native CAD IFC ingestion and RTX scan workflow")
 sub=p.add_subparsers(dest="action",required=True)
 imp=sub.add_parser("import");imp.add_argument("file");imp.add_argument("--name")
 pl=sub.add_parser("plan");pl.add_argument("dataset");pl.add_argument("--profile",choices=PROFILES,default="precision");pl.add_argument("--spacing",type=float,default=3.);pl.add_argument("--target",type=float,default=.99);pl.add_argument("--stations",type=Path);pl.add_argument("--exterior",action="store_true");pl.add_argument("--elevated",action="store_true");pl.add_argument("--all-candidates",action="store_true")
 sc=sub.add_parser("scan");sc.add_argument("dataset");sc.add_argument("--profile",choices=PROFILES,default="precision");sc.add_argument("--resume")
 sv=sub.add_parser("survey");sv.add_argument("dataset");sv.add_argument("--brief",type=Path);sv.add_argument("--run")
 a=p.parse_args()
 with FileLock(str(ROOT/".pipeline.lock")):
  if a.action=="import":import_ifc(a.file,a.name)
  elif a.action=="scan":scan(a.dataset,a.profile,a.resume)
  elif a.action=="plan":
   if not .5<=a.spacing<=10 or not .5<=a.target<=1:raise ValueError("Planner settings out of range")
   from planner import plan
   plan(project(a.dataset),a.profile,a.spacing,a.target,read(a.stations)["stations"] if a.stations else None,a.all_candidates,a.exterior,a.elevated)
  else:
   from survey import build_report,validate_brief
   folder=project(a.dataset)
   if a.brief:write(folder/"survey-brief.json",validate_brief(read(a.brief),bim_metadata(folder)))
   report=build_report(folder,a.run);print("SURVEY_READY",report["status"],len(report["gaps"]))

if __name__=="__main__":main()
