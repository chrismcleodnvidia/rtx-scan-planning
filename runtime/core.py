"""Dataset contracts shared by CLI, scanner workers, and the local viewer."""
from pathlib import Path
import hashlib,json,os,re,sys,time,uuid
ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('BIMSCAN_DATA',ROOT/'data')).resolve()
LOCAL=json.loads((ROOT/'local-config.json').read_text()) if (ROOT/'local-config.json').exists() else {}
PYTHON=Path(os.environ.get('BIMSCAN_PYTHON',sys.executable))
ISAAC=Path(os.environ.get('ISAAC_SIM_PYTHON',LOCAL.get('isaac_sim_python','python.bat' if os.name=='nt' else '/opt/isaac-sim/python.sh')))
PROFILES={
 'dense':dict(title='Dense geometry',channels=512,report_rate=14400,near=.15,far=80.,elevation=[-70,70],material_mode='opaque'),
 'precision':dict(title='Fine angular sampling',channels=1024,report_rate=28800,near=.15,far=80.,elevation=[-70,70],material_mode='opaque'),
 'material':dict(title='Material response · experimental',channels=512,report_rate=14400,near=.15,far=80.,elevation=[-70,70],material_mode='classified'),
 'preview':dict(title='Quick geometry',channels=128,report_rate=7200,near=.15,far=80.,elevation=[-70,70],material_mode='opaque'),
 'terrestrial':dict(title='Terrestrial sampling · uncalibrated',channels=1024,report_rate=25600,near=.5,far=45.,elevation=[-45,90],material_mode='opaque')}
def read(path):return json.loads(Path(path).read_text())
def write(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(value,indent=2,allow_nan=False));temp.replace(path)
def digest(path):
 with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def project(key):
 if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',key):raise ValueError('Invalid dataset identifier')
 p=DATA/key
 if not (p/'project.json').exists():raise ValueError('Dataset not found')
 return p
def command(args,log):
 import subprocess
 with Path(log).open('a') as f:
  f.write('\nCOMMAND '+json.dumps([str(x) for x in args])+'\n');f.flush();result=subprocess.run([str(x) for x in args],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
 if result.returncode:raise RuntimeError('Processing failed; see '+Path(log).name)

def scene_path(folder):
 folder=Path(folder);standalone=folder/'model/building.usdc'
 if not standalone.exists():raise FileNotFoundError('Native CAD USD required: '+str(standalone))
 scan_scene=folder/'model/scan-scene.usda'
 return scan_scene if scan_scene.exists() else standalone

def bim_metadata(folder):
 """Native CAD USD is authority; JSON is a rebuildable transport cache."""
 import subprocess
 folder=Path(folder)
 record=read(folder/'project.json')
 if record.get('metadata_backend')!='nvidia-cad-native':
  raise ValueError('Unsupported metadata backend; import native NVIDIA CAD Converter USD')
 scene=scene_path(folder)
 signature=digest(scene)+'-'+digest(folder/'model/building.usdc')[:12]+'-'+digest(ROOT/'native_ifc.py')[:12]+'-'+record['source_revision']
 cache=ROOT/'.cache/bim-metadata'/(signature+'.json')
 if cache.exists():return read(cache)
 try:
  from native_ifc import metadata
  result=metadata(scene,record['source_revision'])
 except ModuleNotFoundError as e:
  if e.name!='pxr':raise
  result=json.loads(subprocess.check_output([str(PYTHON),str(ROOT/'native_ifc.py'),str(scene),'--source-revision',record['source_revision']],cwd=ROOT,text=True))
 write(cache,result);return result
