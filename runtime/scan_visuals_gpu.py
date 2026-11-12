"""Chunked depth-tested projection kernel; no mesh or inferred surfaces."""
import warp as wp
@wp.kernel
def raster(points:wp.array(dtype=wp.vec3),labels:wp.array(dtype=wp.uint32),eyes:wp.array(dtype=wp.vec3),right:wp.array(dtype=wp.vec3),up:wp.array(dtype=wp.vec3),forward:wp.array(dtype=wp.vec3),scale:wp.array(dtype=float),clip:wp.array(dtype=float),depth:wp.array3d(dtype=wp.uint64),width:int,height:int):
 v,i=wp.tid();p=points[i]
 if p[2]>clip[v]:return
 d=p-eyes[v];z=wp.dot(d,forward[v])
 if z<=0.0 or z>=42949.0:return
 x=int(wp.floor(float(width)*0.5+wp.dot(d,right[v])*scale[v]));y=int(wp.floor(float(height)*0.5-wp.dot(d,up[v])*scale[v]))
 if x<0 or x>=width or y<0 or y>=height:return
 key=(wp.uint64(wp.uint32(z*100000.0))<<wp.uint64(32))|wp.uint64(labels[i])
 wp.atomic_min(depth,v,y,x,key)
