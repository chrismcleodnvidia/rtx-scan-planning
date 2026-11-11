"""Independent triangle queries using Warp, separate from RTX sensor output."""
import numpy as np
import warp as wp
wp.init()
@wp.kernel
def nearest_kernel(mesh:wp.uint64,p:wp.array(dtype=wp.vec3),d:wp.array(dtype=float),q:wp.array(dtype=wp.vec3),face:wp.array(dtype=int)):
 i=wp.tid();h=wp.mesh_query_point_no_sign(mesh,p[i],100.0)
 d[i]=100.0;face[i]=-1
 if h.result:
  a=wp.mesh_eval_position(mesh,h.face,h.u,h.v);q[i]=a;d[i]=wp.length(p[i]-a);face[i]=h.face
@wp.kernel
def rays_kernel(mesh:wp.uint64,origin:wp.vec3,p:wp.array(dtype=wp.vec3),distance:wp.array(dtype=float),face:wp.array(dtype=int)):
 i=wp.tid();v=p[i]-origin;r=wp.length(v);h=wp.mesh_query_ray(mesh,origin,v/r,100.0)
 distance[i]=100.0;face[i]=-1
 if h.result:distance[i]=h.t;face[i]=h.face
class Geometry:
 def __init__(self,path):
  self.data=np.load(path);self.mesh=wp.Mesh(points=wp.array(self.data['vertices'],dtype=wp.vec3,device='cuda'),indices=wp.array(self.data['faces'].ravel(),dtype=int,device='cuda'))
 def nearest(self,points):
  p=wp.array(np.asarray(points,dtype='float32'),dtype=wp.vec3,device='cuda');d=wp.empty(len(p),dtype=float,device='cuda');q=wp.empty(len(p),dtype=wp.vec3,device='cuda');f=wp.empty(len(p),dtype=int,device='cuda');wp.launch(nearest_kernel,len(p),inputs=[self.mesh.id,p,d,q,f],device='cuda');return d.numpy(),q.numpy(),f.numpy()
 def rays(self,origin,points):
  p=wp.array(np.asarray(points,dtype='float32'),dtype=wp.vec3,device='cuda');d=wp.empty(len(p),dtype=float,device='cuda');f=wp.empty(len(p),dtype=int,device='cuda');wp.launch(rays_kernel,len(p),inputs=[self.mesh.id,wp.vec3(*origin),p,d,f],device='cuda');return d.numpy(),f.numpy()
