"""Read IFC metadata already authored by NVIDIA CAD Converter; never edit USD.

This is a scan/viewer consumer, not an IFC converter or metadata enrichment.
Occurrence identities are separate from the type identities on the same prim.
"""
from pathlib import Path
import argparse, hashlib, json
from pxr import Usd, UsdGeom, Gf

PREFIX = 'omni:hoops:metadata:'
NAMES = '''IfcProject IfcSite IfcBuilding IfcBuildingStorey IfcSpace IfcZone
IfcWall IfcWallStandardCase IfcWallElementedCase IfcSlab IfcSlabElementedCase
IfcRoof IfcCovering IfcDoor IfcWindow IfcCurtainWall IfcColumn IfcColumnStandardCase
IfcBeam IfcBeamStandardCase IfcMember IfcPlate IfcRailing IfcStair IfcStairFlight
IfcRamp IfcRampFlight IfcFooting IfcPile IfcBuildingElementProxy IfcBuildingElementPart
IfcElementAssembly IfcFurnishingElement IfcFurniture IfcFlowTerminal IfcFlowSegment
IfcFlowFitting IfcFlowController IfcFlowMovingDevice IfcFlowStorageDevice
IfcEnergyConversionDevice IfcDistributionFlowElement IfcDistributionControlElement
IfcTransportElement IfcOpeningElement IfcVirtualElement IfcGrid IfcAnnotation
IfcReinforcingBar IfcReinforcingMesh IfcMechanicalFastener IfcFastener
IfcGeographicElement IfcCivilElement IfcAlignment'''.split()
CLASS_NAMES = {n.upper(): n for n in NAMES}
NONPHYSICAL = {'IfcSpace', 'IfcOpeningElement', 'IfcVirtualElement', 'IfcAnnotation', 'IfcGrid'}


def json_value(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    try:
        return [json_value(v) for v in value]
    except TypeError:
        return str(value)


def owner_index(meta):
    return {r['usd_prim']: r for r in meta['elements'].values() if r.get('usd_prim')}


def owner(prim, index):
    while prim:
        row = index.get(str(prim.GetPath()))
        if row is not None:
            return row
        prim = prim.GetParent()
    return None


def hide_nonphysical_geometry(stage, meta):
    """Hide space/opening geometry without hiding physical contents of the container.

    Author only into the caller's session or application layer. Native CAD USD
    can nest furniture and doors inside an IfcSpace or IfcOpeningElement.
    Visibility on that ancestor would also suppress its physical descendants.
    """
    owners = owner_index(meta)
    excluded = []
    for prim in list(stage.Traverse()):
        if not prim.IsA(UsdGeom.Gprim):
            continue
        row = owner(prim, owners)
        if row and row['ifc_class'] in NONPHYSICAL:
            UsdGeom.Imageable(prim).CreateVisibilityAttr('invisible')
            excluded.append(str(prim.GetPath()))
    return excluded


def prepare_scan_scene(folder):
    """Application composition for metre-based sensors; original CAD USD stays intact."""
    folder=Path(folder)
    source=folder/'model/building.usdc'
    stage=Usd.Stage.Open(str(source))
    if UsdGeom.GetStageUpAxis(stage) != 'Z':
        raise ValueError('Run CAD Converter with --up-axis z for this scanning application')
    scale=UsdGeom.GetStageMetersPerUnit(stage)
    if scale == 1:
        return source
    if scale <= 0 or not stage.GetDefaultPrim():
        raise ValueError('CAD USD requires positive units and a default prim')
    output=folder/'model/scan-scene.usda'
    composed=Usd.Stage.CreateNew(str(output))
    UsdGeom.SetStageMetersPerUnit(composed,1)
    UsdGeom.SetStageUpAxis(composed,'Z')
    root=UsdGeom.Xform.Define(composed,'/Scan')
    root.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(scale))
    asset=UsdGeom.Xform.Define(composed,'/Scan/Asset')
    asset.GetPrim().GetReferences().AddReference('building.usdc')
    composed.SetDefaultPrim(root.GetPrim())
    composed.GetRootLayer().customLayerData={'purpose':'Scan application unit conversion',
        'nativeMetersPerUnit':scale,'nativeUsdUnmodified':True}
    composed.GetRootLayer().Save()
    return output


def metadata(path, source_revision=None):
    path = Path(path)
    stage = Usd.Stage.Open(str(path))
    if not stage:
        raise ValueError('Could not open native CAD USD: ' + str(path))
    records = {}
    for prim in stage.Traverse():
        names = [n for n in prim.GetPropertyNames() if n.startswith(PREFIX)]
        ids = [n for n in names if n.endswith(':GlobalId') and
               n[len(PREFIX):].split(':')[0].startswith('IFC') and
               not n[len(PREFIX):].split(':')[0].endswith(('TYPE', 'STYLE'))]
        if not ids:
            continue
        if len(ids) != 1:
            raise ValueError('Ambiguous native occurrence identity: ' + str(prim.GetPath()))
        identity_name = ids[0]
        native_class = identity_name[len(PREFIX):].split(':')[0]
        guid = prim.GetAttribute(identity_name).Get()
        if not isinstance(guid, str) or not guid:
            continue
        if guid in records:
            raise ValueError('Repeated occurrence identity requires an instance-aware consumer: ' + guid)
        groups = {}
        for name in names:
            attr = prim.GetAttribute(name)
            if not attr:
                continue
            group, _, field = name[len(PREFIX):].partition(':')
            groups.setdefault(group, {})[field] = dict(value=json_value(attr.Get()),
                usd_attribute=name, usd_type=str(attr.GetTypeName()))
        attributes = {k: v['value'] for k, v in groups.get(native_class, {}).items()}
        row = dict(guid=guid, ifc_class=CLASS_NAMES.get(native_class, native_class),
            native_ifc_class=native_class, name=attributes.get('Name', prim.GetName()),
            attributes=attributes, usd_prim=str(prim.GetPath()),
            property_sets=[dict(name=g, properties=v) for g, v in groups.items()],
            materials=groups.get('IFCMATERIAL', {}), surface_styles=[],
            relationships=[dict(name=r.GetName(), targets=[str(t) for t in r.GetTargets()])
                           for r in prim.GetRelationships() if 'hoops' in r.GetName()],
            container=None, scan_surface=False, geometry_kind='metadata_only',
            identity_attribute=identity_name)
        records[guid] = row
    if not records:
        raise ValueError('No native IFC occurrence metadata; enable --convert-metadata in CAD Converter')
    result = dict(authority='USD', metadata_backend='nvidia-cad-native',
        source_ifc_sha256=source_revision,
        source_usd_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        revision_kind='source IFC' if source_revision else 'USD asset',
        usd_asset_root=str(stage.GetDefaultPrim().GetPath()),
        world_offset_m=[0, 0, 0], elements=records,
        reference_resolution='Values and relationships read directly from native CAD-converter USD. No supplementary IFC metadata.')
    index = owner_index(result)
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            row = owner(prim, index)
            if row:
                row['scan_surface'] = row['ifc_class'] not in NONPHYSICAL
                row['geometry_kind'] = 'mesh'
    for row in records.values():
        parent = stage.GetPrimAtPath(row['usd_prim']).GetParent()
        while parent:
            container = index.get(str(parent.GetPath()))
            if container and container['ifc_class'] == 'IfcBuildingStorey':
                row['container'] = dict(guid=container['guid'], name=container['name'],
                    usd_prim=container['usd_prim'], basis='native USD hierarchy')
                break
            parent = parent.GetParent()
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('usd'); p.add_argument('--source-revision')
    a = p.parse_args()
    print(json.dumps(metadata(a.usd, a.source_revision)))
