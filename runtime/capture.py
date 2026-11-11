"""Offline RTX captures using NVIDIA's installed LidarRtx/StableIdMap pipeline.

Run with Isaac Sim's python.sh. No surface sampling or inferred object labels.
Each NPZ stores native world coordinates, intensity, timing and stable IDs.
"""
from pathlib import Path
import argparse,hashlib,json,time

parser=argparse.ArgumentParser()
parser.add_argument('--scene',type=Path,required=True)
parser.add_argument('--metadata',type=Path,help='Legacy only; omitted for self-contained building USD')
parser.add_argument('--native-ifc',action='store_true',help='Read native NVIDIA CAD Converter IFC properties directly from USD')
parser.add_argument('--source-revision',help='Original IFC hash for capture provenance; not a metadata input')
parser.add_argument('--material-mode',choices=['opaque','classified'],default='opaque')
parser.add_argument('--resume',action='store_true')
parser.add_argument('--stations',type=Path,required=True)
parser.add_argument('--output-dir',type=Path,required=True)
parser.add_argument('--vertical-channels',type=int,default=512)
parser.add_argument('--report-rate',type=int,default=14400)
parser.add_argument('--near-range',type=float,default=.15)
parser.add_argument('--far-range',type=float,default=80.)
parser.add_argument('--elevation-min',type=float,default=-70.)
parser.add_argument('--elevation-max',type=float,default=70.)
parser.add_argument('--capture-seconds',type=float,default=1.)
parser.add_argument('--min-packets',type=int,default=60)
parser.add_argument('--range-noise',type=float,default=0.)
parser.add_argument('--angular-noise',type=float,default=0.)
args=parser.parse_args();started=time.perf_counter()
if (args.output_dir/'capture.json').exists() and not args.resume:raise RuntimeError('Refusing to overwrite a recorded capture without --resume')

def save_json(path,value):
    temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(value,indent=2));temp.replace(path)

def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

from isaacsim import SimulationApp
app=SimulationApp({'headless':True,'enable_motion_bvh':True,
    'extra_args':['--/rtx-transient/stableIds/enabled=true',
        '--/rtx/materialDb/nonVisualMaterialSemantics/prefix=inputs:nonvisual']})
try:
    import numpy as np
    import omni.timeline,omni.usd
    from pxr import Usd,UsdGeom,Sdf,Gf
    from isaacsim.core.utils.stage import open_stage
    from isaacsim.sensors.rtx import LidarRtx,get_gmo_data,apply_nonvisual_material
    stations=json.loads(args.stations.read_text())['stations']
    source=args.scene.resolve()
    open_stage(str(source));stage=omni.usd.get_context().get_stage()
    from native_ifc import metadata as native_metadata,owner_index,owner as resolve_owner,hide_nonphysical_geometry
    if args.native_ifc:
        metadata=native_metadata(source,args.source_revision)
    elif stage.GetDefaultPrim().GetAttribute('bim:mappingVersion'):
        from ifc_usd.read import metadata as usd_metadata
        metadata=usd_metadata(source)
    elif args.metadata:
        metadata=json.loads(args.metadata.read_text())
    else:
        raise ValueError('Legacy USD requires --metadata; new self-contained USD does not')
    stage.SetEditTarget(stage.GetSessionLayer())
    owners=owner_index(metadata)
    box=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render','proxy']).ComputeWorldBound(stage.GetDefaultPrim()).ComputeAlignedRange()
    source_min=np.asarray(box.GetMin());source_max=np.asarray(box.GetMax())
    excluded_nonphysical=hide_nonphysical_geometry(stage,metadata)
    # Capture material response separately from visual finish; retain existing
    # bindings. Material-level overrides only affect this session layer.
    guide_prims=[]
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Imageable) and UsdGeom.Imageable(prim).GetPurposeAttr().Get()=='guide':
            UsdGeom.Imageable(prim).CreateVisibilityAttr('invisible');guide_prims.append(str(prim.GetPath()))
    sensor_policy={'rules':[],'assumption':'All opaque concrete for geometry-only baseline'}
    if args.material_mode=='classified':
        sensor_policy={'rules':[{'name_contains':['glass','glazing'],'base':'clear_glass'},{'name_contains':['aluminium','aluminum'],'base':'aluminum'},{'name_contains':['steel','metal'],'base':'steel'},{'name_contains':['plastic','polymer'],'base':'plastic'},{'name_contains':['fabric','upholstery'],'base':'fabric'},{'name_contains':['wood','timber'],'base':'wood'}]}
    sensor_policy["excluded_guide_prims"]=guide_prims
    sensor_policy['excluded_nonphysical_ifc_prims']=excluded_nonphysical
    sensor_policy['nonphysical_exclusion_policy']='Hide nonphysical-owned geometry only; retain physical contents of space/opening containers'
    from pxr import UsdShade
    from isaacsim.core.api.materials import OmniPBR
    material_records={}
    for prim in list(stage.Traverse()):
        if prim.GetTypeName()!='Material':continue
        name=str(prim.GetPath()).lower();base='concrete'
        for rule in sensor_policy['rules']:
            if any(word in name for word in rule['name_contains']):base=rule['base'];break
        material=OmniPBR(prim_path=str(prim.GetPath()),name='sensor_'+prim.GetName(),color=np.array([.3+len(material_records)*.0001,.5,.6]));assert apply_nonvisual_material(material.prim,base,'none','none')
        material_records[str(prim.GetPath())]=base
    unbound=[]
    fallback=UsdShade.Material.Define(stage,'/Sensors/Nonvisual/UnspecifiedConcrete')
    assert apply_nonvisual_material(fallback.GetPrim(),'concrete','none','none')
    shader=UsdShade.Shader.Define(stage,'/Sensors/Nonvisual/UnspecifiedConcrete/Shader')
    shader.CreateIdAttr('UsdPreviewSurface');shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.65));fallback.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),'surface')
    for prim in list(stage.Traverse()):
        if prim.GetTypeName()!='Mesh':continue
        if not UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]:
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(fallback);unbound.append(str(prim.GetPath()))
    lidar_prim=stage.DefinePrim('/Sensors/Lidar','OmniLidar')
    lidar_prim.SetMetadata('apiSchemas',Sdf.TokenListOp.Create(prependedItems=['OmniSensorGenericLidarCoreAPI']))
    xf=UsdGeom.Xformable(lidar_prim);move=xf.AddTranslateOp()
    config={'elementsCoordsType':('token','CARTESIAN'),'outputFrameOfReference':('token','WORLD'),
        'auxOutputType':('token','FULL'),'skipDroppingInvalidPoints':('bool',False),
        'nearRangeM':('float',args.near_range),'farRangeM':('float',args.far_range),
        'reportRateBaseHz':('uint',args.report_rate),'scanRateBaseHz':('uint',10),
        'numberOfEmitters':('uint',args.vertical_channels),'numberOfChannels':('uint',args.vertical_channels),'maxReturns':('uint',1),'rangeAccuracyM':('float',args.range_noise),'azimuthErrorStd':('float',args.angular_noise),'elevationErrorStd':('float',args.angular_noise),'azimuthErrorMean':('float',0.),'elevationErrorMean':('float',0.)}
    types={'token':Sdf.ValueTypeNames.Token,'bool':Sdf.ValueTypeNames.Bool,'float':Sdf.ValueTypeNames.Float,'uint':Sdf.ValueTypeNames.UInt}
    for name,(typ,value) in config.items():lidar_prim.CreateAttribute('omni:sensor:Core:'+name,types[typ]).Set(value)
    for name,values,typ in [('elevationDeg',np.linspace(args.elevation_min,args.elevation_max,args.vertical_channels).tolist(),Sdf.ValueTypeNames.FloatArray),
                            ('azimuthDeg',[0.]*args.vertical_channels,Sdf.ValueTypeNames.FloatArray)]:
        lidar_prim.CreateAttribute('omni:sensor:Core:emitterState:s001:'+name,typ).Set(values)
    if args.vertical_channels != 128:
        lidar_prim.CreateAttribute('omni:sensor:Core:emitterState:s001:channelId',Sdf.ValueTypeNames.UIntArray).Set(list(range(1,args.vertical_channels+1)))
        fire_times=(np.floor(np.arange(args.vertical_channels)*8/args.vertical_channels).astype(int)*3500).tolist()
        lidar_prim.CreateAttribute('omni:sensor:Core:emitterState:s001:fireTimeNs',Sdf.ValueTypeNames.UIntArray).Set(fire_times)
    lidar=LidarRtx(prim_path='/Sensors/Lidar',name='refinement_stations')
    lidar.initialize();lidar.attach_annotator('GenericModelOutput');lidar.attach_annotator('StableIdMap')
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    stage.GetSessionLayer().Export(str(out/'sensor-session.usda'))
    timeline=omni.timeline.get_timeline_interface();timeline.play()
    source_files={layer.realPath:digest(layer.realPath) for layer in stage.GetUsedLayers() if layer.realPath}
    captures=[];labels={};label_rows=[]
    if args.resume and (out/'capture.json').exists():
        previous=json.loads((out/'capture.json').read_text())
        assert previous['source_scene_sha256']==digest(source) and previous['source_revision']==metadata['source_ifc_sha256'], 'Resume source mismatch'
        assert previous['sensor_config']==json.loads(json.dumps(config)), 'Resume scanner configuration mismatch'
        captures=previous['stations'];label_rows=json.loads((out/'labels.json').read_text());labels={r['prim_path']:r['index'] for r in label_rows}
        expected={s['id']:s['position_m'] for s in stations}
        for item in captures:
            assert digest(out/item['file'])==item['sha256'], 'Resume checkpoint corrupted'
            assert expected.get(item['id'])==item['position_m'], 'Resume station plan changed'
        for path,sha in previous['source_usd_layers'].items():assert digest(path)==sha, 'Resume dependency changed'
        record=previous
        if captures:materials=np.load(out/captures[-1]['file'])['material_id']
        started-=previous.get('elapsed_seconds',0)
    completed={s['id'] for s in captures}
    for station in stations:
        if station['id'] in completed:
            print('RESUME_SKIP',station['id'],flush=True);continue
        move.Set(Gf.Vec3d(*station['position_m']))
        for _ in range(12):app.update()
        chunks=[];stable={};timestamps=[];rejected_packets=0;invalid_native_returns=0;native_flag_counts={};packet_diagnostics=[]
        for _ in range(1200):
            app.update();frame=lidar.get_current_frame()
            # Annotator buffers are reused asynchronously. Own a snapshot for
            # the entire decode; keeping the dictionary does not pin its bytes.
            packet=np.array(frame['GenericModelOutput'],copy=True)
            gmo=get_gmo_data(packet)
            if int(gmo.timestampNs) in timestamps:continue
            stable.update(LidarRtx.decode_stable_id_mapping(frame['StableIdMap'].tobytes()))
            # Native IDs are 16 raw little-endian bytes. Isaac 5.1's helper
            # views those bytes as Unicode and fails for arbitrary valid IDs.
            # Decode the documented wire representation without text coercion.
            if np.asarray(gmo.objId).nbytes!=len(gmo.x)*16:
                rejected_packets+=1;continue
            raw_ids=np.asarray(gmo.objId).view(np.uint8).reshape(-1,16).copy()
            chunk=dict(ids=[int.from_bytes(row.tobytes(),'little') for row in raw_ids],
                points=np.column_stack([gmo.x,gmo.y,gmo.z]).astype(np.float32),
                intensity=np.asarray(gmo.scalar).copy(),flags=np.asarray(gmo.flags).copy(),
                times=np.asarray(gmo.timeOffSetNs).copy(),materials=np.asarray(gmo.matId).copy(),
                timestamp_ns=np.full(len(gmo.x),int(gmo.timestampNs),np.uint64))
            for flag,count in zip(*np.unique(chunk['flags'],return_counts=True)):
                native_flag_counts[str(flag)]=native_flag_counts.get(str(flag),0)+int(count)
            valid=(chunk['flags']==64)&np.isfinite(chunk['points']).all(1)
            invalid_native_returns+=int((~valid).sum())
            # A valid native return must use the requested coordinates and lie
            # within the scene/range. Reject a malformed packet wholesale and
            # acquire a replacement; never move or trim points to fit geometry.
            hits=chunk['points'][valid]
            distances=np.linalg.norm(hits-np.asarray(station['position_m']),axis=1)
            outside=((hits<source_min-.01)|(hits>source_max+.01)).any(1)
            out_of_range=(distances>args.far_range+.01)|(distances<args.near_range-.01)
            wrong_frame=str(gmo.frameOfReference)!='FrameOfReference.WORLD' or str(gmo.elementsCoordsType)!='CoordsType.CARTESIAN'
            if wrong_frame or outside.any() or out_of_range.any():
                rejected_packets+=1
                detail=dict(timestamp_ns=int(gmo.timestampNs),frame=str(gmo.frameOfReference),coordinates=str(gmo.elementsCoordsType),outside_scene=int(outside.sum()),outside_range=int(out_of_range.sum()))
                packet_diagnostics.append(detail)
                evidence=out/'rejected-packets';evidence.mkdir(exist_ok=True)
                np.savez_compressed(evidence/(station['id']+'-'+str(int(gmo.timestampNs))+'.npz'),points=chunk['points'],flags=chunk['flags'],intensity=chunk['intensity'])
                save_json(evidence/(station['id']+'.json'),packet_diagnostics)
                print('REJECTED_CAPTURE_PACKET',station['id'],detail,flush=True)
                continue
            if any(v not in stable or v==0 for v,k in zip(chunk['ids'],valid) if k):
                if rejected_packets<3:print('UNRESOLVED_PACKET',len(chunk['points']),[(v,str(stable.get(v))) for v,k in zip(chunk['ids'],valid) if k and (v not in stable or v==0)][:3],flush=True)
                rejected_packets+=1;continue
            chunk={key:([v for v,k in zip(value,valid) if k] if key=='ids' else value[valid]) for key,value in chunk.items()}
            chunks.append(chunk)
            timestamps.append(int(gmo.timestampNs))
            if len(chunks)>=args.min_packets and max(timestamps)-min(timestamps)>=args.capture_seconds*1e9:break
        if len(chunks)<args.min_packets or max(timestamps)-min(timestamps)<args.capture_seconds*1e9:
            failure=dict(station=station['id'],chunks=len(chunks),rejected_packets=rejected_packets,invalid_native_returns=invalid_native_returns,native_flag_counts=native_flag_counts)
            (out/'failure.json').write_text(json.dumps(failure,indent=2));raise RuntimeError('Insufficient capture duration: '+str(failure))
        # Timestamps are authoritative. A stationary surface can legitimately
        # produce identical coordinates on successive complete revolutions.
        ids=[value for chunk in chunks for value in chunk['ids']]
        points=np.concatenate([c['points'] for c in chunks]);intensity=np.concatenate([c['intensity'] for c in chunks])
        flags=np.concatenate([c['flags'] for c in chunks]);times=np.concatenate([c['times'] for c in chunks])
        materials=np.concatenate([c['materials'] for c in chunks]);timestamps_per_point=np.concatenate([c['timestamp_ns'] for c in chunks])
        if not len(points):raise RuntimeError('RTX produced no returns at '+station['id'])
        print('GMO',station['id'],len(points),'frame',gmo.frameOfReference,'coords',gmo.elementsCoordsType,
              'flags',np.unique(flags).tolist(),'range',points.min(axis=0).tolist(),points.max(axis=0).tolist(),flush=True)
        # Resolve each native ID once, then gather labels with a linear lookup.
        # Comparing every point with every mesh ID is costly on furnished buildings.
        id_to_label={}
        for value in set(ids):
            if not value or value not in stable:raise RuntimeError('Unresolved native stable object ID: '+str(value))
            path=str(stable[value]);row=resolve_owner(stage.GetPrimAtPath(path),owners)
            if row is None:raise RuntimeError('Native return has no IFC occurrence: '+path)
            if path not in labels:
                labels[path]=len(label_rows)
                label_rows.append(dict(index=labels[path],prim_path=path,
                    ifc_guid=row['guid'],ifc_class=row['ifc_class'],name=row['name'],
                    identity_method='native RTX stable object ID → USD prim → ancestor IFC metadata'))
            id_to_label[value]=labels[path]
        index=np.fromiter((id_to_label[v] for v in ids),dtype=np.uint32,count=len(ids))
        assert np.isfinite(points).all() and np.isfinite(intensity).all()
        low=np.array([int(v)&((1<<64)-1) for v in ids],np.uint64)
        high=np.array([int(v)>>64 for v in ids],np.uint64)
        target=out/(station['id']+'.npz')
        np.savez_compressed(target,points=points,intensity=intensity,time_offset_ns=times,frame_timestamp_ns=timestamps_per_point,flags=flags,
            material_id=materials,stable_id_low=low,stable_id_high=high,label_index=index)
        captures.append(dict(**station,points=len(points),file=target.name,sha256=digest(target),
            capture_duration_s=(max(timestamps)-min(timestamps))/1e9,nominal_revolutions=(max(timestamps)-min(timestamps))/1e9*10,sensor_to_world=np.asarray(xf.GetLocalTransformation()).tolist(),native_frame=str(gmo.frameOfReference),native_coords=str(gmo.elementsCoordsType),frame_timestamps_ns=timestamps,
            rejected_malformed_packets=rejected_packets,rejected_coordinate_packets=packet_diagnostics,invalid_native_returns=invalid_native_returns,native_flag_counts=native_flag_counts))
        for item in label_rows:
            guid=item['ifc_guid']
            if guid:
                assert guid in metadata['elements'],guid
                assert item['ifc_class']==metadata['elements'][guid]['ifc_class']
                item['source_revision']=metadata['source_ifc_sha256']
                item['metadata_key']=guid
        save_json(out/'labels.json',label_rows)
        (out/'ifc-metadata.json').write_text(json.dumps(metadata,indent=2))
        record=dict(validation_status='pending_final_checks',status='complete' if len(captures)==len(stations) else 'partial',backend='Isaac Sim 5.1 RTX LidarRtx + GenericModelOutput + StableIdMap',
            source_scene=str(source),source_scene_sha256=digest(source),source_usd_layers=source_files,
            source_revision=metadata['source_ifc_sha256'],
            units='metres',up_axis='Z',coordinate_frame='native WORLD',sensor_config=config,
            elapsed_seconds=time.perf_counter()-started,elevation_degrees=[args.elevation_min,args.elevation_max],vertical_channels=args.vertical_channels,frames_per_station=len(chunks),stations=captures,
            limitations=['Synthetic generic rotary sensor; no sensor-specific calibration','Range/angular noise settings recorded in sensor_config; not a vendor calibration',
                'Unobserved surfaces remain absent','Glass and material response are assumptions, not measured calibration',
                'No claim of training benefit; learning target unspecified'])
        save_json(out/'capture.json',record)
        print('CAPTURED',station['id'],len(points),flush=True)
    assert np.any(materials!=0), 'Nonvisual material IDs were not encoded by the sensor'
    (out/'sensor-materials.json').write_text(json.dumps(dict(policy=sensor_policy,assignments=material_records,unbound_mesh_fallback=unbound),indent=2))
    record['validation_status']='passed_capture_checks';save_json(out/'capture.json',record)
    timeline.stop()
except BaseException:
    import traceback
    traceback.print_exc()
    raise
finally:app.close()
