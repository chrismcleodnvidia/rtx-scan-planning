"""World-coordinate USD mesh extraction with native IFC identity ancestors."""
from core import bim_metadata,scene_path
from pathlib import Path
import json,numpy as np
from pxr import Usd,UsdGeom,Gf
from core import read,write
from native_ifc import owner_index,owner as resolve_owner,NONPHYSICAL
def prepare(folder):
 folder=Path(folder);stage=Usd.Stage.Open(str(scene_path(folder)));metadata=bim_metadata(folder);cache=UsdGeom.XformCache();scale=UsdGeom.GetStageMetersPerUnit(stage)
 owners=owner_index(metadata)
 if UsdGeom.GetStageUpAxis(stage)!='Z' or abs(scale-1)>1e-9:raise ValueError('Scan contract requires metres/Z-up; normalize the stage before scanning')
 vertices=[];faces=[];face_labels=[];labels=[];meshes=[];vo=fo=0;guid_index={}
 for prim in stage.Traverse():
  if not prim.IsA(UsdGeom.Mesh) or UsdGeom.Imageable(prim).ComputeVisibility()=='invisible':continue
  row=resolve_owner(prim,owners)
  if row is None:raise ValueError('Mesh has no preserved IFC identity: '+str(prim.GetPath()))
  if row['ifc_class'] in NONPHYSICAL:continue
  guid=row['guid']
  if guid not in metadata['elements']:raise ValueError('IFC metadata missing: '+guid)
  if guid not in guid_index:
   guid_index[guid]=len(labels);row=metadata['elements'][guid];labels.append(dict(index=len(labels),guid=guid,name=row['name'],ifc_class=row['ifc_class']))
  idx=guid_index[guid];mesh=UsdGeom.Mesh(prim);points=np.asarray(mesh.GetPointsAttr().Get(),dtype='float64');matrix=np.asarray(cache.GetLocalToWorldTransform(prim));points=(np.c_[points,np.ones(len(points))]@matrix)[:,:3].astype('float32');counts=np.asarray(mesh.GetFaceVertexCountsAttr().Get());indices=np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
  if not np.all(counts==3):raise ValueError('Expected triangulated conversion output')
  tri=indices.reshape(-1,3);vertices.append(points);faces.append(tri+vo);face_labels.append(np.full(len(tri),idx,'int32'));meshes.append(dict(label=idx,path=str(prim.GetPath()),vertex_start=vo,vertex_count=len(points),index_start=fo*3,index_count=len(tri)*3));vo+=len(points);fo+=len(tri)
 if not vertices:raise ValueError('No visible IFC meshes')
 v=np.concatenate(vertices);f=np.concatenate(faces).astype('int32');fl=np.concatenate(face_labels);geo=folder/'geometry';geo.mkdir(exist_ok=True);np.savez_compressed(geo/'mesh.npz',vertices=v,faces=f,face_labels=fl);write(geo/'labels.json',labels)
 web=folder/'view';web.mkdir(exist_ok=True);v.astype('<f4').tofile(web/'vertices.bin');f.astype('<u4').tofile(web/'indices.bin');write(web/'geometry.json',dict(meshes=meshes,labels=labels,bounds_m=[v.min(0).tolist(),v.max(0).tolist()],vertex_count=len(v),triangle_count=len(f),source_revision=metadata['source_ifc_sha256']))
 # Separate planning and measurement samples prevent scoring the plan on its own samples.
 tri=v[f];cross=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);area=np.linalg.norm(cross,axis=1)/2;norm=cross/np.maximum(area[:,None]*2,1e-20)
 for name,n,seed in [('planning',20000,20261103),('audit',100000,20261104)]:
  rng=np.random.default_rng(seed);chosen=rng.choice(len(f),n,p=area/area.sum());uv=rng.random((n,2));u=np.sqrt(uv[:,0]);t=tri[chosen];p=(1-u[:,None])*t[:,0]+(u*(1-uv[:,1]))[:,None]*t[:,1]+(u*uv[:,1])[:,None]*t[:,2];np.savez_compressed(geo/(name+'.npz'),points=p.astype('float32'),normals=norm[chosen],label_index=fl[chosen],sample_area_m2=float(area.sum()/n))
 p=read(folder/'project.json')
 p.update(status='ready',source_revision=metadata['source_ifc_sha256'],bounds_m=[v.min(0).tolist(),v.max(0).tolist()],elements=len(labels),triangles=len(f),world_offset_m=metadata.get('world_offset_m',[0,0,0]));write(folder/'project.json',p);print('GEOMETRY_READY',len(labels),len(f),flush=True)
