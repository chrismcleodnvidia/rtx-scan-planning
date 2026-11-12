"""IFC-specific lookup helpers for a viewer's existing picking/selection API."""
import argparse,json
from pathlib import Path

def nearest_owner(path,elements):
    owners={row['usd_prim']:guid for guid,row in elements.items() if row.get('usd_prim')}
    return _nearest(path,owners)
def _nearest(path,owners):
    path=str(path)
    while path and path!='/':
        if path in owners:return owners[path]
        path=path.rsplit('/',1)[0]
    return None

def mesh_index(stage,elements):
    """Renderable mesh -> closest IFC owner. Do not colour only parent Xforms."""
    from pxr import UsdGeom
    owners={r['usd_prim']:g for g,r in elements.items() if r.get('usd_prim')}
    index={}
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            owner=_nearest(prim.GetPath(),owners)
            if owner:index[str(prim.GetPath())]=owner
    return index

def scan_owner(labels,index,elements):
    if index<0 or index>=len(labels):raise ValueError('Scan label index out of range')
    guid=labels[index].get('ifc_guid')
    if guid not in elements:raise ValueError('Scan label has no matching IFC owner in this USD')
    return guid

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--usd',type=Path,required=True)
    q=p.add_mutually_exclusive_group(required=True);q.add_argument('--prim');q.add_argument('--scan-label',type=int);q.add_argument('--mesh-index',action='store_true')
    p.add_argument('--labels',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
    from native_ifc import metadata
    meta=metadata(a.usd);elements=meta['elements']
    if a.mesh_index:
        from pxr import Usd
        result=mesh_index(Usd.Stage.Open(str(a.usd)),elements)
    else:
        if a.scan_label is not None:
            if not a.labels:p.error('--scan-label requires --labels')
            guid=scan_owner(json.loads(a.labels.read_text()),a.scan_label,elements)
        else:guid=nearest_owner(a.prim,elements)
        result={'ifc_guid':guid,'element':elements.get(guid)}
    text=json.dumps(result,indent=2)
    if a.output:a.output.write_text(text)
    else:print(text)
if __name__=='__main__':main()
