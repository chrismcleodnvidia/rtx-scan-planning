"""Portable preview and full-capture image export; no workbench UI dependency."""
import argparse, colorsys, hashlib, json, math, time
from pathlib import Path
import numpy as np

DTYPE = np.dtype([('x','<f4'),('y','<f4'),('z','<f4'),('intensity','<f4'),('ifc_label','<u4')])
PROPERTIES = ['property float x','property float y','property float z','property float intensity','property uint ifc_label']
def read_ply(path, expected):
    with Path(path).open('rb') as f:
        lines=[]
        for _ in range(100):
            line=f.readline().decode('ascii').strip();lines.append(line)
            if line=='end_header':break
        else:raise ValueError('PLY header too long or missing end_header')
        offset=f.tell()
    if lines[0]!='ply' or 'format binary_little_endian 1.0' not in lines:raise ValueError('Expected binary little-endian PLY')
    if [x for x in lines if x.startswith('property ')]!=PROPERTIES:raise ValueError('Expected x/y/z/intensity floats and uint ifc_label, in that order')
    if [x for x in lines if x.startswith('element ')]!=[f'element vertex {expected}']:raise ValueError('PLY count/elements differ from capture manifest')
    if Path(path).stat().st_size-offset!=expected*DTYPE.itemsize:raise ValueError('PLY payload length differs from header')
    return np.memmap(path,dtype=DTYPE,mode='r',offset=offset,shape=(expected,)) if expected else np.empty(0,dtype=DTYPE)
def capture(run):
    run=Path(run);manifest=json.loads((run/'capture.json').read_text());labels=json.loads((run/'labels.json').read_text())
    if not manifest.get('stations'):raise ValueError('No stations')
    revision=manifest.get('source_revision')
    for i,label in enumerate(labels):
        if label.get('index',i)!=i:raise ValueError('Labels must be indexed in array order')
        if label.get('source_revision') not in (None,revision):raise ValueError('Mixed source revisions')
    return manifest,labels

def chunks(run,manifest,label_count,size):
    if size<1:raise ValueError('Chunk size must be positive')
    for station in manifest['stations']:
        name=station['id']
        if Path(name).name!=name or '/' in name or '\\' in name:raise ValueError('Station ID must be a filename component')
        data=read_ply(Path(run)/(name+'.ply'),station['points'])
        for start in range(0,len(data),size):
            a=data[start:start+size];xyz=np.column_stack([a['x'],a['y'],a['z']]);ids=np.ascontiguousarray(a['ifc_label'])
            if not np.isfinite(xyz).all() or (len(ids) and ids.max()>=label_count):raise ValueError('Nonfinite point or unresolved label')
            yield xyz,ids

def colour(name):
    return colorsys.hsv_to_rgb(int(hashlib.sha256(name.encode()).hexdigest()[:6],16)/0xffffff,.65,.9)
def preview(run,output,budget=400000,chunk_size=1000000):
    if budget<1:raise ValueError('Budget must be positive')
    manifest,labels=capture(run);total=sum(s['points'] for s in manifest['stations']);stride=max(1,math.ceil(total/budget));offset=0;points=[];indices=[]
    for xyz,ids in chunks(run,manifest,len(labels),chunk_size):
        start=(-offset)%stride;points.append(xyz[start::stride].copy());indices.append(ids[start::stride].copy());offset+=len(xyz)
    xyz=np.concatenate(points) if points else np.empty((0,3),np.float32);ids=np.concatenate(indices) if indices else np.empty(0,np.uint32)
    output=Path(output);output.mkdir(parents=True,exist_ok=True);np.savez_compressed(output/'preview.npz',points=xyz,label_index=ids)
    report=dict(source_revision=manifest.get('source_revision'),input_points=total,processed_points=offset,display_points=len(xyz),fraction=len(xyz)/total if total else 0,budget=budget,stride=stride,method='Every nth point in concatenated capture order; deterministic, not a coverage-preserving or spatially uniform sample.')
    (output/'labels.json').write_text(json.dumps(labels,indent=2));(output/'preview.json').write_text(json.dumps(report,indent=2));return report

def camera(spec,width,height):
    eye=np.asarray(spec['eye'],float);target=np.asarray(spec['target'],float);up_hint=np.asarray(spec.get('up',[0,0,1]),float)
    forward=target-eye;norm=np.linalg.norm(forward)
    if not np.isfinite([*eye,*target,*up_hint]).all() or norm<1e-8:raise ValueError('Invalid eye/target')
    forward/=norm;right=np.cross(forward,up_hint)
    if np.linalg.norm(right)<1e-8:raise ValueError('Camera up is parallel to viewing direction')
    right/=np.linalg.norm(right);up=np.cross(right,forward);span=float(spec['vertical_span_m'])
    if not math.isfinite(span) or span<=0:raise ValueError('vertical_span_m must be positive')
    return eye,right,up,forward,height/span,float(spec.get('clip_z_max',1e9))

def render(run,views,output,width=2560,height=1920,chunk_size=1000000,device='cuda:0'):
    # Import optional rendering dependencies only for render, not preview/help.
    import warp as wp
    from PIL import Image
    try:
        from .scan_visuals_gpu import raster
    except ImportError:
        from scan_visuals_gpu import raster
    if width<1 or height<1:raise ValueError('Resolution must be positive')
    manifest,labels=capture(run);names=[v['name'] for v in views]
    if not names or len(set(names))!=len(names) or any(not n or not all(c.isalnum() or c in '-_' for c in n) for n in names):raise ValueError('Unique safe view names required')
    cams=[camera(v,width,height) for v in views];wp.init()
    arrays=[wp.array(np.asarray([c[i] for c in cams],np.float32),dtype=wp.vec3 if i<4 else wp.float32,device=device) for i in range(6)]
    z=wp.array(np.full((len(cams),height,width),np.iinfo(np.uint64).max,np.uint64),dtype=wp.uint64,device=device)
    total=0;started=time.monotonic()
    for xyz,ids in chunks(run,manifest,len(labels),chunk_size):
        p=wp.array(xyz,dtype=wp.vec3,device=device);li=wp.array(ids,dtype=wp.uint32,device=device)
        wp.launch(raster,dim=(len(cams),len(xyz)),inputs=[p,li,*arrays,z,width,height],device=device);wp.synchronize();total+=len(xyz)
    expected=sum(s['points'] for s in manifest['stations']);assert total==expected
    result=z.numpy();palette=np.asarray([colour(r.get('ifc_class') or 'Unknown') for r in labels]);output=Path(output);output.mkdir(parents=True,exist_ok=True);counts=[]
    for i,name in enumerate(names):
        keys=result[i];valid=keys!=np.iinfo(np.uint64).max;ids=(keys&np.uint64(0xffffffff)).astype(np.uint32);rgb=np.zeros((height,width,3),np.uint8);rgb[:]=[13,22,34]
        if valid.any():
            depth=(keys[valid]>>np.uint64(32)).astype(float)/100000;lo,hi=np.percentile(depth,[2,98]);shade=np.clip(1-.32*(depth-lo)/max(hi-lo,.001),.65,1)
            rgb[valid]=(palette[ids[valid]]*shade[:,None]*255).astype(np.uint8)
        Image.fromarray(rgb).save(output/(name+'.png'));counts.append(int(valid.sum()))
    report=dict(source_revision=manifest.get('source_revision'),input_points=expected,processed_points=total,stations=len(manifest['stations']),complete=True,resolution=[width,height],elapsed_seconds=time.monotonic()-started,views=views,occupied_pixels=counts,method='All recorded PLY points; orthographic one-pixel depth-tested projection, 10-micrometre depth key. No mesh filling or point decimation. Camera framing and explicit clipping apply; overlapping points share pixels. IFC class colours with distance shading. This is a Warp point rasterizer, not an OVRTX photorealistic render.')
    (output/'render.json').write_text(json.dumps(report,indent=2));return report

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    for action in ('preview','render'):
        q=sub.add_parser(action);q.add_argument('--run',type=Path,required=True);q.add_argument('--output',type=Path,required=True);q.add_argument('--chunk-size',type=int,default=1000000)
        if action=='preview':q.add_argument('--budget',type=int,default=400000)
        else:q.add_argument('--views',type=Path,required=True);q.add_argument('--width',type=int,default=2560);q.add_argument('--height',type=int,default=1920);q.add_argument('--device',default='cuda:0')
    a=p.parse_args()
    result=preview(a.run,a.output,a.budget,a.chunk_size) if a.action=='preview' else render(a.run,json.loads(a.views.read_text()),a.output,a.width,a.height,a.chunk_size,a.device)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
