"""Source-linked draft survey briefs and evidence reports; no certification implied."""
from core import bim_metadata,scene_path
from collections import Counter
from pathlib import Path
import hashlib
import html
import json
import math
import numpy as np
from core import read, write

GUIDANCE = 'https://historicengland.org.uk/images-books/publications/geospatial-survey-specifications-cultural-heritage/'


def default_brief(revision):
    return dict(schema_version=1, source_revision=revision,
        purpose='Synthetic building survey for IFC-linked data generation',
        scope_description='Review all supplied physical IFC elements, interiors, exterior elevations and roof. Confirm required exposed faces; buried interfaces are not automatically survey scope.',
        requirements_status='draft_pending_customer_requirements',
        required_guids=[], exclusions={}, critical_guids=[],
        critical_classes=['IfcDoor', 'IfcWindow', 'IfcColumn', 'IfcBeam', 'IfcStair', 'IfcStairFlight'],
        support_tolerance_mm=20., element_support_target=.99,
        point_spacing_mm=5., measurement_tolerance_mm=5.,
        minimum_audit_samples=30, minimum_views=2,
        notes='', criteria_basis='Provisional experiment values. 5 mm spacing/precision is inspired by the Historic England 1:50 terrestrial example; support and view-count targets are local diagnostics, not an AEC standard.')


def validate_brief(value, metadata):
    expected=default_brief(metadata['source_ifc_sha256'])
    if not isinstance(value,dict) or set(value)-set(expected):
        raise ValueError('Unknown survey brief fields')
    b=expected | value
    if b['source_revision'] != metadata['source_ifc_sha256']:
        raise ValueError('Survey brief belongs to a different IFC revision')
    if b['requirements_status'] != 'draft_pending_customer_requirements':
        raise ValueError('This prototype records draft requirements; it does not certify client acceptance')
    for key in ['purpose','scope_description','notes','criteria_basis']:
        if not isinstance(b[key],str) or len(b[key])>5000:raise ValueError('Invalid '+key)
    for key,lo,hi in [('support_tolerance_mm',1,100),('element_support_target',.5,1),('point_spacing_mm',.1,100),('measurement_tolerance_mm',.1,100),('minimum_audit_samples',1,10000),('minimum_views',1,10)]:
        x=b[key]
        if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or not lo<=x<=hi:raise ValueError('Invalid '+key)
    for key in ['minimum_audit_samples','minimum_views']:
        if int(b[key])!=b[key]:raise ValueError('Expected integer '+key)
        b[key]=int(b[key])
    for key in ['required_guids','critical_guids']:
        if not isinstance(b[key],list) or any(not isinstance(g,str) or g not in metadata['elements'] for g in b[key]):raise ValueError('Unknown IFC identity in '+key)
    if not isinstance(b['critical_classes'],list) or any(not isinstance(c,str) or not c.startswith('Ifc') for c in b['critical_classes']):raise ValueError('Invalid critical classes')
    if not isinstance(b['exclusions'],dict) or any(g not in metadata['elements'] or not isinstance(reason,str) or not reason.strip() for g,reason in b['exclusions'].items()):raise ValueError('Each exclusion requires a known IFC identity and a reason')
    if set(b['critical_guids']) & set(b['exclusions']):raise ValueError('A critical element cannot also be excluded')
    return b


def brief_hash(brief):
    return hashlib.sha256(json.dumps(brief,sort_keys=True).encode()).hexdigest()


def ensure_brief(folder):
    folder=Path(folder); meta=bim_metadata(folder);path=folder/'survey-brief.json'
    b=validate_brief(read(path) if path.exists() else {},meta)
    if not path.exists():write(path,b)
    return b


def required_indices(labels, brief):
    requested=set(brief['required_guids'])
    return [r['index'] for r in labels if (not requested or r['guid'] in requested) and r['guid'] not in brief['exclusions']]


def element_visibility(labels, sample_labels, reachable, covered, counts, brief):
    """Keep unseen and unsampled objects; never silently drop them from the report."""
    required=set(required_indices(labels,brief));out=[]
    for label in labels:
        mask=sample_labels==label['index'];r=mask & reachable;c=mask & covered
        n=int(mask.sum());nr=int(r.sum());nc=int(c.sum())
        out.append(dict(guid=label['guid'],required=label['index'] in required,
            reference_samples=n,candidate_visible_samples=nr,selected_visible_samples=nc,
            selected_of_candidate_visible=nc/nr if nr else None,
            selected_of_reference=nc/n if n else None,
            minimum_views_fraction=float(np.mean(counts[r]>=brief['minimum_views'])) if nr else None))
    return out


def build_report(folder, run_id=None):
    folder=Path(folder);meta=bim_metadata(folder);labels=read(folder/'geometry/labels.json')
    b=ensure_brief(folder); plan=read(folder/'plan.json') if (folder/'plan.json').exists() else None
    run=None;metrics={};evidence=None
    if run_id:
        run=folder/'runs'/run_id
        if run.parent.resolve()!=(folder/'runs').resolve():raise ValueError('Invalid capture identifier')
        metrics=read(run/'metrics.json')
        if (run/'survey-brief.json').exists():b=validate_brief(read(run/'survey-brief.json'),meta)
        else:metrics=dict(metrics,retrospective_criteria=True)
        if (run/'stations.json').exists():plan=read(run/'stations.json')
        if (run/'coverage-audit.npz').exists():evidence=np.load(run/'coverage-audit.npz')
    if plan and plan['source_revision']!=meta['source_ifc_sha256']:raise ValueError('Stale station plan')
    audit=np.load(folder/'geometry/audit.npz'); measured={r['guid']:r for r in metrics.get('objects',[])}
    planned={r['guid']:r for r in (plan or {}).get('element_visibility',[])}
    required=set(required_indices(labels,b));elements=[]
    for label in labels:
        guid=label['guid'];row=meta['elements'][guid];m=measured.get(guid);p=planned.get(guid,{})
        n=int(np.sum(audit['label_index']==label['index']));observed=m.get('samples',0) if m else 0
        support=m.get('visible_support_2cm') if m and b['support_tolerance_mm']==20 else None
        if evidence is not None:
            mask=(audit['label_index']==label['index']) & evidence['visible'];observed=int(mask.sum())
            support=float(np.mean(evidence['nearest'][mask]<=b['support_tolerance_mm']/1000)) if observed else None
        status='not_captured'
        if run:
            status=('not_observed' if not observed else 'insufficient_samples' if observed<b['minimum_audit_samples'] else 'tolerance_not_evaluated' if support is None else 'below_target' if support<b['element_support_target'] else 'sample_support_target_met')
        if label['index'] not in required:status='outside_draft_scope'
        elements.append(dict(guid=guid,name=row['name'],ifc_class=row['ifc_class'],
            container_guid=(row.get('container') or {}).get('guid'),required=label['index'] in required,
            critical=guid in b['critical_guids'] or row['ifc_class'] in b['critical_classes'],
            reference_audit_samples=n,visible_audit_samples=observed,support_fraction=support,
            status=status,planning=p,exclusion_reason=b['exclusions'].get(guid)))
    spaces=[dict(guid=g,name=r['name'],status='room_boundary_and_station_assignment_required') for g,r in meta['elements'].items() if r['ifc_class']=='IfcSpace']
    levels=[dict(guid=g,name=r['name'],elements=sum(e['container_guid']==g for e in elements)) for g,r in meta['elements'].items() if r['ifc_class']=='IfcBuildingStorey']
    gaps=[]
    for e in elements:
        if not e['required']:continue
        reason=None
        if e['status'] not in ['not_captured','sample_support_target_met']:reason=e['status']
        elif e['planning'].get('candidate_visible_samples')==0:reason='no_candidate_visibility'
        elif e['planning'].get('minimum_views_fraction',1) is not None and e['planning'].get('minimum_views_fraction',1)<b['element_support_target']:reason='repeat_view_gap'
        elif e['planning'].get('selected_of_candidate_visible',1) is not None and e['planning'].get('selected_of_candidate_visible',1)<b['element_support_target']:reason='planning_visibility_gap'
        if reason:gaps.append(dict(guid=e['guid'],name=e['name'],priority='critical' if e['critical'] else 'normal',reason=reason,action='Review required exposed faces; add viewpoints for occlusion gaps, increase angular sampling for sparse returns, or record a justified exclusion. Unsampled small features need targeted measurement.'))
    rendered={l['guid'] for l in labels}
    unresolved=[g for g in b['required_guids'] if g not in rendered]
    unresolved_critical=[g for g in b['critical_guids'] if g not in rendered]
    report=dict(schema_version=1,source_revision=meta['source_ifc_sha256'],brief_sha256=brief_hash(b),
        status='draft_not_professionally_accepted',run_id=run_id,brief=b,
        inventory=dict(rendered_elements=len(labels),classes=dict(Counter(e['ifc_class'] for e in elements)),spaces=spaces,storeys=levels,required_without_scan_geometry=unresolved,critical_without_scan_geometry=unresolved_critical),
        stations=(plan or {}).get('stations',[]),elements=elements,gaps=gaps,
        element_status_counts=dict(Counter(e['status'] for e in elements if e['required'])),
        measurement=dict(support_tolerance_mm=b['support_tolerance_mm'],point_spacing_status='not_measured',physical_accuracy_status='not_validated',geometry_error=metrics.get('surface_error'),first_hit_error=metrics.get('first_hit_error'),retrospective_criteria=metrics.get('retrospective_criteria',False)),
        unresolved_requirements=['Confirm purpose, deliverables, required rooms/elevations/roof and exposed faces with customer',
            'Verify room boundaries and assign stations; IFC storey containment is not room membership',
            'Measure local point spacing separately from distance-to-surface support',
            'Review glass/reflective materials and scanner calibration; simulation is not a certified survey',
            'Review independent control/registration and checkpoints for comparison with real scans',
            'Review uncovered and undersampled elements, including small critical features'],
        workflow=['Audit IFC identities, units, coordinate frame and geometry before planning',
            'Review draft scope, priorities and exclusions before evaluating completeness',
            'Plan floor positions and optional exterior/elevated synthetic positions; no count cap',
            'Select extra stations to address individual element visibility and second views',
            'Capture with fixed source revision and snapshot the survey brief',
            'Evaluate independent surface samples, geometry error, labels and gap register',
            'Iterate positions/sampling and review gaps; a global percentage alone never closes the survey'],
        guidance=GUIDANCE)
    if metrics.get('first_hit_query'):report['measurement']['first_hit_query']=metrics['first_hit_query']
    dest=run if run else folder
    write(dest/'survey-plan.json',report);(dest/'survey-plan.html').write_text(render_report(report))
    if run and (folder/'view'/run.name/'run.json').exists():
        view=folder/'view'/run.name/'run.json';summary=read(view);summary['survey_report']=True;write(view,summary)
    return report


def render_report(r):
    esc=lambda x:html.escape(str(x if x is not None else '—'))
    pct=lambda x:'—' if x is None else f'{x:.1%}'
    b=r['brief'];rows=''.join(f'<tr><td>{esc(e["name"])}<br><code>{esc(e["guid"])}</code></td><td>{esc(e["ifc_class"])}</td><td>{esc(e["status"])}</td><td>{e["visible_audit_samples"]} / {e["reference_audit_samples"]}</td><td>{pct(e["support_fraction"])}</td></tr>' for e in sorted(r['elements'],key=lambda e:(not e['critical'],e['status'],e['guid'])))
    stations=''.join(f'<tr><td>{esc(s["id"])}</td><td>{esc(s["position_m"])}</td><td>{esc(s.get("group"))}</td></tr>' for s in r['stations'])
    gaps=''.join(f'<li><b>{esc(g["priority"])}: {esc(g["name"])}</b> — {esc(g["reason"])}. {esc(g["action"])}</li>' for g in r['gaps']) or '<li>No measured gaps recorded yet; this does not establish completeness.</li>'
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Building survey plan</title><style>body{{font:16px system-ui;color:#163149;max-width:1200px;margin:40px auto;padding:20px}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:9px;border-bottom:1px solid #ccd6df;text-align:left}}code{{font-size:11px}}.draft{{background:#fff1cf;padding:16px}}li{{margin:10px 0}}@media print{{body{{margin:0}}}}</style>
    <h1>Building survey plan</h1><p class="draft">Draft for customer review. No professional acceptance or field-scanner calibration is claimed.</p>
    <p><b>Source revision:</b> <code>{esc(r['source_revision'])}</code><br><b>Capture:</b> {esc(r['run_id'])}</p>
    <h2>Purpose and scope</h2><p>{esc(b['purpose'])}</p><p>{esc(b['scope_description'])}</p><p>{esc(b['notes'])}</p>
    <p>{len(r['inventory']['spaces'])} named IFC spaces; {len(r['inventory']['storeys'])} storeys; {r['inventory']['rendered_elements']} rendered IFC elements. Room boundaries and station membership remain to be verified.</p>
    <h2>Spatial inventory</h2><ul>{''.join('<li>'+esc(x['name'])+' — room boundary and station assignment not verified</li>' for x in r['inventory']['spaces']) or '<li>No IFC spaces supplied; request a room schedule or reviewed spatial subdivision.</li>'}</ul><ul>{''.join('<li>'+esc(x['name'])+' — '+str(x['elements'])+' directly contained elements</li>' for x in r['inventory']['storeys'])}</ul>
    <h2>Provisional criteria</h2><p>Surface support: {pct(b['element_support_target'])} per element within {b['support_tolerance_mm']} mm, with at least {b['minimum_audit_samples']} audit samples for a usable diagnostic. {b['minimum_views']} views desired where candidates permit. These are experiment settings, not universal survey standards.</p>
    <p>Point spacing requested: {b['point_spacing_mm']} mm (not yet measured). Physical measurement tolerance requested: ±{b['measurement_tolerance_mm']} mm (not validated). {esc(b['criteria_basis'])}</p>
    <h2>Method and review</h2><ol>{''.join('<li>'+esc(x)+'</li>' for x in r['workflow'])}</ol>
    <h2>Outstanding requirements</h2><ul>{''.join('<li>'+esc(x)+'</li>' for x in r['unresolved_requirements'])}</ul>
    <p>Required IDs without scan geometry: {esc(r['inventory']['required_without_scan_geometry'])}. Critical IDs without geometry: {esc(r['inventory']['critical_without_scan_geometry'])}.</p>
    <h2>Station schedule ({len(r['stations'])})</h2><p>Positions use the local USD metre frame. Exterior/elevated positions are synthetic; physical access is not implied.</p><table><tr><th>Station</th><th>XYZ metres</th><th>Method</th></tr>{stations}</table>
    <h2>Element evidence</h2><p>Visible / reference counts are independent area samples, not ray counts. Full model surfaces can include buried faces. No room-completeness claim follows from a visible-surface score. Non-20 mm criteria on older captures require stored detailed audit evidence or remeasurement.</p><table><tr><th>Element / IFC ID</th><th>Class</th><th>Status</th><th>Visible / reference samples</th><th>Support</th></tr>{rows}</table>
    <h2>Gap register</h2><ul>{gaps}</ul><h2>Geometry checks</h2><pre>{esc(json.dumps(r['measurement'],indent=2))}</pre>
    <p><a href="{GUIDANCE}">Historic England survey specification and brief template</a> — reference guidance; customer requirements remain authoritative.</p></html>'''
