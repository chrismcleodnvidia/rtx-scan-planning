"""Plan extra RTX viewpoints using the recorded sensor's angular sampling grid.

Predictions guide placement only. Native RTX captures and the independent coverage
audit remain the measurement. Source IFC and CAD USD are never modified here.
"""
import numpy as np


class BeamGrid:
    def __init__(self, capture):
        cfg = capture['sensor_config']
        self.near = cfg['nearRangeM'][1]
        self.far = cfg['farRangeM'][1]
        self.azimuth_step = 2*np.pi*cfg['scanRateBaseHz'][1]/cfg['reportRateBaseHz'][1]
        self.elevation = np.radians(np.linspace(*capture['elevation_degrees'], cfg['numberOfChannels'][1]))
        self.elevation_step = self.elevation[1]-self.elevation[0]
        if cfg['rangeAccuracyM'][1] or cfg['azimuthErrorStd'][1] or cfg['elevationErrorStd'][1]:
            raise ValueError('This placement predictor requires the retained zero-noise scan profile')
        for station in capture['stations']:
            if not np.allclose(np.asarray(station['sensor_to_world'])[:3,:3],np.eye(3)):
                raise ValueError('Rotated sensor grids require an explicit orientation model')

    def support(self, geometry, origin, sample, tolerance=.018):
        """Return visibility and conservative four-neighbour beam support masks."""
        origin = np.asarray(origin,dtype='float64')
        points = np.asarray(sample['points'],dtype='float64')
        delta = points-origin
        distance = np.linalg.norm(delta,axis=1)
        direction = delta/np.maximum(distance[:,None],1e-12)
        azimuth = np.arctan2(delta[:,1],delta[:,0])
        elevation = np.arctan2(delta[:,2],np.linalg.norm(delta[:,:2],axis=1))
        incidence = np.abs(np.sum(direction*sample['normals'],axis=1))
        eligible = ((distance>=self.near)&(distance<=min(self.far,99.))
                    &(elevation>=self.elevation[0])&(elevation<=self.elevation[-1])
                    &(incidence>=np.cos(np.radians(80))))
        ix = np.flatnonzero(eligible)
        visible = np.zeros(len(points),dtype=bool)
        supported = np.zeros(len(points),dtype=bool)
        if not len(ix):return visible,supported
        hits,faces = geometry.rays(origin,points[ix])
        ix = ix[(faces>=0)&(np.abs(hits-distance[ix])<.002)]
        visible[ix] = True
        if not len(ix):return visible,supported
        az_floor = np.floor(azimuth[ix]/self.azimuth_step)
        el_floor = np.floor((elevation[ix]-self.elevation[0])/self.elevation_step).astype(int)
        if not hasattr(geometry,'_density_face_labels'):
            geometry._density_face_labels = geometry.data['face_labels']
        face_labels = geometry._density_face_labels
        target_labels = sample['label_index'][ix]
        for da,de in [(0,0),(0,1),(1,0),(1,1)]:
            channel = el_floor+de
            valid = (channel>=0)&(channel<len(self.elevation))&~supported[ix]
            active = np.flatnonzero(valid)
            if not len(active):continue
            az = (az_floor[active]+da)*self.azimuth_step
            el = self.elevation[channel[active]]
            ray = np.column_stack((np.cos(el)*np.cos(az),np.cos(el)*np.sin(az),np.sin(el)))
            # A long direction segment avoids losing angular precision when
            # the asset uses kilometre-scale world coordinates.
            hit,face = geometry.rays(origin,origin+ray*80.)
            owner = np.full(len(face),-1,dtype=np.int64)
            ok = face>=0
            owner[ok] = face_labels[face[ok]]
            location = origin+ray*hit[:,None]
            close = np.linalg.norm(location-points[ix[active]],axis=1)<=tolerance
            supported[ix[active]] |= (ok&(hit>=self.near)&(hit<=self.far)
                                      &(owner==target_labels[active])&close)
        return visible,supported


def plan_density(folder,output,base_run=None,target=.999,tolerance=.018):
    """Use all retained candidate positions; no scan-count cap or scope exclusion."""
    from pathlib import Path
    import hashlib,json,time
    from core import read,write,digest
    from geometry_ops import Geometry
    folder=Path(folder);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    project=read(folder/'project.json');base=folder/'runs'/(base_run or project['preferred_run'])
    capture=read(base/'capture.json');beam=BeamGrid(capture)
    candidates=read(folder/'station-candidates.json')['stations']
    sample_data=np.load(folder/'geometry/planning.npz')
    sample={key:sample_data[key] for key in ['points','normals','label_index']}
    signature=dict(source_revision=project['source_revision'],base_run=base.name,
                   base_capture_sha256=digest(base/'capture.json'),
                   candidates_sha256=digest(folder/'station-candidates.json'),
                   planning_sha256=digest(folder/'geometry/planning.npz'),
                   predictor_sha256=digest(Path(__file__)),tolerance_m=tolerance,target=target)
    progress_path=output/'progress.json'
    if progress_path.exists():
        progress=read(progress_path)
        if progress['signature']!=signature:raise ValueError('Density-plan inputs changed; use a new output directory')
    else:
        progress=dict(signature=signature,status='predicting',next_candidate=0,started=time.time());write(progress_path,progress)
    g=Geometry(folder/'geometry/mesh.npz')
    original=output/'base-predictions.npz'
    if not original.exists():
        base_visible=np.zeros(len(sample['points']),bool);base_support=base_visible.copy()
        for i,station in enumerate(capture['stations']):
            visible,support=beam.support(g,station['position_m'],sample,tolerance)
            base_visible|=visible;base_support|=support
            if i%50==0:print('BASE_DENSITY',i+1,len(capture['stations']),flush=True)
        np.savez_compressed(original,visible=base_visible,support=base_support)
    else:
        with np.load(original) as data:base_visible=data['visible'];base_support=data['support']
    shape=(len(candidates),len(sample['points']))
    mode='r+' if (output/'support.npy').exists() else 'w+'
    masks=np.lib.format.open_memmap(output/'support.npy',mode=mode,dtype=bool,shape=shape)
    views=np.lib.format.open_memmap(output/'visibility.npy',mode=mode,dtype=bool,shape=shape)
    for i in range(progress['next_candidate'],len(candidates)):
        views[i],masks[i]=beam.support(g,candidates[i]['position_m'],sample,tolerance)
        if (i+1)%100==0 or i+1==len(candidates):
            masks.flush();views.flush();progress['next_candidate']=i+1;write(progress_path,progress)
            print('CANDIDATE_DENSITY',i+1,len(candidates),flush=True)
    visible=base_visible|np.any(views,axis=0)
    covered=base_support.copy();selected=[];available=np.ones(len(candidates),bool)
    # Greedy gains are updated only for newly covered samples. This avoids
    # rescanning every candidate against every sample on each iteration.
    gains=np.sum(masks[:,visible&~covered],axis=1,dtype=np.int64)
    while available.any() and np.sum(covered&visible)<target*np.sum(visible):
        best=int(np.argmax(np.where(available,gains,-1)))
        if gains[best]<=0:break
        newly=masks[best]&visible&~covered
        selected.append(best);covered|=masks[best];available[best]=False
        gains-=np.sum(masks[:,newly],axis=1,dtype=np.int64)
    # Retain the existing per-element 99% intent where planning has enough
    # samples to make that percentage meaningful. Small objects remain in
    # the capture and independent per-element report, without being excluded.
    owners=sample['label_index'];major=[]
    for owner in np.unique(owners[visible]):
        indices=np.flatnonzero(visible&(owners==owner))
        if len(indices)>=30:major.append(indices)
    while available.any():
        deficit=np.zeros(len(visible),bool)
        for indices in major:
            if np.mean(covered[indices])<.99:deficit[indices[~covered[indices]]]=True
        if not deficit.any():break
        scores=np.sum(masks[:,deficit],axis=1,dtype=np.int64);scores[~available]=-1
        best=int(np.argmax(scores))
        if scores[best]<=0:break
        selected.append(best);covered|=masks[best];available[best]=False
    stations=[]
    for ordinal,index in enumerate(selected,1):
        station=dict(candidates[index]);station['candidate_id']=station['id'];station['id']=f'D{ordinal:04}'
        station['selection_reason']='Fill predicted point-density gaps across all asset surfaces'
        stations.append(station)
    result=dict(dataset=project['id'],base_run=base.name,source_revision=project['source_revision'],
                candidate_count=len(candidates),stations=stations,scan_count_limit=None,
                planning_tolerance_m=tolerance,measurement_tolerance_m=.02,
                planning_samples=len(visible),candidate_visible_samples=int(visible.sum()),
                before_predicted_support=float(np.mean(base_support[visible])),
                after_predicted_support=float(np.mean(covered[visible])),
                target=target,source_geometry_unchanged=True,
                scope='All physical asset surfaces; no terrain, road, courtyard or other source-element exclusion.',
                method='Four neighbouring rays of the retained RTX angular grid; actual first-hit IFC owner required. Predictions guide placement, not reported capture coverage.')
    write(output/'plan.json',result)
    progress.update(status='checking_independent_samples',selected_positions=len(stations));write(progress_path,progress)
    # This fresh check is distinct from both optimisation samples and the
    # retained post-capture audit used for the published measurements.
    validation=output/'independent-planning-check.npz'
    if not validation.exists():
        v=g.data['vertices'];faces=g.data['faces'];tri=v[faces]
        cross=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);area=np.linalg.norm(cross,axis=1)/2
        rng=np.random.default_rng(2026110828);n=60000;chosen=rng.choice(len(faces),n,p=area/area.sum())
        uv=rng.random((n,2));u=np.sqrt(uv[:,0]);t=tri[chosen]
        points=((1-u[:,None])*t[:,0]+(u*(1-uv[:,1]))[:,None]*t[:,1]+(u*uv[:,1])[:,None]*t[:,2]).astype('float32')
        np.savez_compressed(validation,points=points,normals=cross[chosen]/np.maximum(area[chosen,None]*2,1e-20),label_index=g.data['face_labels'][chosen])
        del tri,cross,area,t
    with np.load(validation) as data:check={k:data[k] for k in ['points','normals','label_index']}
    check_visible=np.zeros(len(check['points']),bool);check_support=check_visible.copy()
    for i,station in enumerate(capture['stations']+stations):
        vis,support=beam.support(g,station['position_m'],check,.02)
        check_visible|=vis;check_support|=support
        if i%100==0:print('INDEPENDENT_DENSITY_CHECK',i+1,len(capture['stations'])+len(stations),flush=True)
    result['independent_planning_check']=dict(seed=2026110828,samples=len(check_visible),
        visible_samples=int(check_visible.sum()),predicted_support_20mm=float(np.mean(check_support[check_visible])),
        scope='Uncaptured prediction on a separate sample set; the original100000-point capture audit is unchanged.')
    np.savez_compressed(output/'independent-predictions.npz',visible=check_visible,supported=check_support)
    write(output/'plan.json',result);progress.update(status='complete',finished=time.time(),independent_check=result['independent_planning_check']);write(progress_path,progress)
    print('DENSITY_PLAN_READY',json.dumps({k:v for k,v in result.items() if k!='stations'}),flush=True)
    return result


if __name__=='__main__':
    from pathlib import Path
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('dataset');parser.add_argument('--output',type=Path,required=True);parser.add_argument('--base-run');parser.add_argument('--target',type=float,default=.999);parser.add_argument('--planning-tolerance',type=float,default=.018)
    args=parser.parse_args()
    if not .99<=args.target<=1 or not 0<args.planning_tolerance<=.02:raise ValueError('Density planning must retain the2cm measurement criterion and at least99% planning target')
    from core import project
    plan_density(project(args.dataset),args.output,args.base_run,args.target,args.planning_tolerance)
