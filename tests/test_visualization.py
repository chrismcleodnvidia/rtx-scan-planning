import json,sys,tempfile,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from scan_visuals import DTYPE,preview,render,read_ply
from ifc_inspection import nearest_owner,mesh_index,scan_owner
class Visuals(unittest.TestCase):
 def fixture(self,folder):
  folder=Path(folder);a=np.zeros(4,dtype=DTYPE);a['x']=[0,0,1,-1];a['y']=[0,0,0,0];a['z']=[1,0,0,0];a['ifc_label']=[0,1,1,1]
  header='ply\nformat binary_little_endian 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\nproperty float intensity\nproperty uint ifc_label\nend_header\n'
  with (folder/'S1.ply').open('wb') as f:f.write(header.encode());a.tofile(f)
  (folder/'capture.json').write_text(json.dumps({'source_revision':'fixture','stations':[{'id':'S1','points':4}]}));(folder/'labels.json').write_text(json.dumps([{'index':0,'ifc_class':'IfcWall','ifc_guid':'wall'},{'index':1,'ifc_class':'IfcSlab','ifc_guid':'floor'}]));return folder
 def test_preview_chunk_independent(self):
  with tempfile.TemporaryDirectory() as t:
   r=self.fixture(t);one=preview(r,r/'a',3,1);two=preview(r,r/'b',3,3)
   self.assertEqual(one,two);self.assertEqual(one['processed_points'],4);self.assertLessEqual(one['display_points'],3)
   np.testing.assert_array_equal(np.load(r/'a/preview.npz')['label_index'],[0,1])
 def test_count_mismatch_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   r=self.fixture(t)
   with self.assertRaises(ValueError):read_ply(r/'S1.ply',5)
 def test_depth_and_cutaway(self):
  from PIL import Image
  with tempfile.TemporaryDirectory() as t:
   r=self.fixture(t);v={'name':'all','eye':[0,0,5],'target':[0,0,0],'up':[0,1,0],'vertical_span_m':4}
   report=render(r,[v,dict(v,name='cut',clip_z_max=.5)],r/'images',32,32,1,'cpu');self.assertEqual(report['processed_points'],4)
   a=np.array(Image.open(r/'images/all.png'));b=np.array(Image.open(r/'images/cut.png'));self.assertFalse(np.array_equal(a[16,16],b[16,16]));self.assertEqual(report['occupied_pixels'],[3,3])
 def test_ifc_nearest_owner(self):
  from pxr import Usd,UsdGeom
  stage=Usd.Stage.CreateInMemory();UsdGeom.Xform.Define(stage,'/Root');UsdGeom.Mesh.Define(stage,'/Root/Wall/Shape');UsdGeom.Mesh.Define(stage,'/Root/Wall/Window/Shape')
  elements={'wall':{'usd_prim':'/Root/Wall'},'window':{'usd_prim':'/Root/Wall/Window'}}
  self.assertEqual(nearest_owner('/Root/Wall/Window/Shape',elements),'window');self.assertEqual(mesh_index(stage,elements)['/Root/Wall/Shape'],'wall');self.assertEqual(scan_owner([{'ifc_guid':'wall'}],0,elements),'wall')
  with self.assertRaises(ValueError):scan_owner([{'ifc_guid':'other'}],0,elements)
if __name__=='__main__':unittest.main()
