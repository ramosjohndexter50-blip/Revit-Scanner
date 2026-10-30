# -*- coding: utf-8 -*-
"""Linear dimensions from selected host geometry; model writes require explicit Apply."""
from __future__ import unicode_literals
import math
from hamdan_editing import atomic_edit, id_value
from hamdan_elements import safe_name, text_type

MAX_ELEMENTS = 200
MAX_REFERENCES = 128
EPS = 1e-6  # Internal feet, used only for coincident reference planes.
CATEGORIES = ('OST_Walls', 'OST_Grids', 'OST_Columns', 'OST_StructuralColumns', 'OST_Doors', 'OST_Windows',
              'OST_Casework', 'OST_Furniture', 'OST_GenericModel', 'OST_SpecialityEquipment', 'OST_MechanicalEquipment')


def xyz(point):
    return (point.X, point.Y, point.Z)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def subtract(a, b):
    return tuple(x - y for x, y in zip(a, b))


def unit(vector):
    length = math.sqrt(dot(vector, vector))
    if length <= EPS:
        raise ValueError('A reference has no usable direction.')
    return tuple(x / length for x in vector)


def supported_category(DB, element):
    category = getattr(element, 'Category', None)
    return category is not None and id_value(category.Id) in [int(getattr(DB.BuiltInCategory, name)) for name in CATEGORIES]


def view_frame(DB, view):
    allowed = (DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.EngineeringPlan,
               DB.ViewType.Section, DB.ViewType.Elevation, DB.ViewType.Detail)
    if view.IsTemplate or view.ViewType not in allowed:
        raise ValueError('Choose a plan, section, elevation or detail view placed on a sheet.')
    origin = view.Origin
    # ViewPlan.Origin does not define its elevation. Use the associated level in project coordinates.
    if isinstance(view, DB.ViewPlan) and view.GenLevel is not None:
        origin = DB.XYZ(origin.X, origin.Y, view.GenLevel.ProjectElevation)
    return {'origin': xyz(origin), 'right': unit(xyz(view.RightDirection)),
            'up': unit(xyz(view.UpDirection)), 'normal': unit(xyz(view.ViewDirection)), 'scale': int(view.Scale)}


def coordinates(point, frame):
    relative = subtract(point, frame['origin'])
    return (dot(relative, frame['right']), dot(relative, frame['up']))


def world_point(frame, x, y):
    return tuple(frame['origin'][i] + frame['right'][i] * x + frame['up'][i] * y for i in range(3))


def paper_offset(value, scale):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError('Offset must be a number in paper millimetres.')
    if math.isnan(value) or math.isinf(value) or not 0 < value <= 100:
        raise ValueError('Paper offset must be greater than 0 and at most 100 mm.')
    if scale < 1:
        raise ValueError('The view has no valid drawing scale.')
    return value * scale / 304.8


def geometry_records(DB, document, element, view, frame, axes, include_edges=False):
    """Keep original geometry references. GetInstanceGeometry copies cannot anchor dimensions."""
    result = []
    label = '{} | {} | ID {}'.format(element.Category.Name, safe_name(element), id_value(element.Id))

    def add(reference, point, normal, kind=None):
        if reference is None or id_value(reference.ElementId) != id_value(element.Id):
            return
        normal = unit(normal)
        for axis in axes:
            vector = frame['right'] if axis == 'Horizontal' else frame['up']
            if abs(dot(normal, vector)) < 1 - 1e-8:
                continue
            row = {'stable': reference.ConvertToStableRepresentation(document),
                   'owner': element.UniqueId, 'label': label, 'axis': axis,
                   'position': dot(subtract(point, frame['origin']), vector)}
            if kind is not None:
                row.update(reference_kind=kind, point=point)
            result.append(row)

    if isinstance(element, DB.Grid):
        curve = element.Curve
        if not isinstance(curve, DB.Line):
            return []
        direction = unit(xyz(curve.Direction))
        if abs(dot(direction, frame['normal'])) > 1e-8:
            return []
        # A grid element reference is the native dimension anchor, rather than a curve copy.
        tangent = DB.XYZ(*direction)
        normal = xyz(tangent.CrossProduct(DB.XYZ(*frame['normal'])))
        add(DB.Reference(element), xyz(curve.GetEndPoint(0)), normal)
        return result

    def walk(geometry, transform, depth=0):
        if depth > 8:
            raise ValueError('Nested geometry exceeds eight levels.')
        for item in geometry:
            if isinstance(item, DB.Solid):
                for face in item.Faces:
                    if isinstance(face, DB.PlanarFace) and face.Area > EPS:
                        add(face.Reference, xyz(transform.OfPoint(face.Origin)), xyz(transform.OfVector(face.FaceNormal)),
                            'Face' if include_edges else None)
                if include_edges:
                    for edge in item.Edges:
                        curve = edge.AsCurve()
                        if not isinstance(curve, DB.Line):
                            continue
                        tangent = unit(xyz(transform.OfVector(curve.Direction)))
                        if abs(dot(tangent, frame['normal'])) > 1e-8:
                            continue
                        normal = DB.XYZ(*tangent).CrossProduct(DB.XYZ(*frame['normal']))
                        add(edge.Reference, xyz(transform.OfPoint(curve.GetEndPoint(0))), xyz(normal), 'Edge')
            elif isinstance(item, DB.GeometryInstance):
                # No transform overload: original symbol geometry retains dimensionable references.
                walk(item.GetSymbolGeometry(), transform.Multiply(item.Transform), depth + 1)

    options = DB.Options()
    options.ComputeReferences, options.IncludeNonVisibleObjects, options.View = True, False, view
    try:
        geometry = element.get_Geometry(options)
        if geometry is not None:
            walk(geometry, DB.Transform.Identity)
    finally:
        options.Dispose()
    return result


def element_bounds(element, view, frame):
    box = element.get_BoundingBox(view)
    if box is None:
        # Datum elements can have usable native references without a view bounding box.
        # Curve endpoints only position the dimension line; the anchor remains Reference(grid).
        curve = getattr(element, 'Curve', None)
        if curve is None:
            raise ValueError('No bounding box is available in the chosen view.')
        points = [coordinates(xyz(curve.GetEndPoint(i)), frame) for i in (0, 1)]
        return [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
    points = [coordinates(xyz(box.Transform.OfPoint(type(box.Min)(x, y, z))), frame)
              for x in (box.Min.X, box.Max.X) for y in (box.Min.Y, box.Max.Y) for z in (box.Min.Z, box.Max.Z)]
    return [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]


def reference_groups(records, frame, bounds, axes, mode, offset_mm, side):
    if mode not in ('Chain', 'Overall') or side not in ('Positive', 'Negative', 'AboveLeft', 'BelowRight'):
        raise ValueError('Choose Chain/Overall and a valid outside placement.')
    if not axes or any(axis not in ('Horizontal', 'Vertical') for axis in axes) or len(set(axes)) != len(axes):
        raise ValueError('Choose horizontal, vertical or both directions.')
    offset = paper_offset(offset_mm, frame['scale'])
    groups, notes = [], []
    for axis in axes:
        selected, seen = [], set()
        for record in sorted((r for r in records if r['axis'] == axis), key=lambda r: (r['position'], r['stable'])):
            if record['stable'] in seen or (selected and abs(record['position'] - selected[-1]['position']) <= EPS):
                continue
            if math.isnan(record['position']) or math.isinf(record['position']):
                raise ValueError('A geometry reference has an invalid location.')
            seen.add(record['stable'])
            selected.append(record)
        if len(selected) < 2:
            notes.append(axis + ': fewer than two distinct parallel references; no dimension planned.')
            continue
        if mode == 'Overall':
            selected = [selected[0], selected[-1]]
        if len(selected) > MAX_REFERENCES:
            raise ValueError('Too many chain references. Choose fewer elements or Overall.')
        index = 1 if axis == 'Horizontal' else 0
        positive = side == 'Positive' or (side == 'AboveLeft' and axis == 'Horizontal') or (side == 'BelowRight' and axis == 'Vertical')
        baseline = bounds[index + 2] + offset if positive else bounds[index] - offset
        start, end = selected[0]['position'], selected[-1]['position']
        a, b = ((start, baseline), (end, baseline)) if axis == 'Horizontal' else ((baseline, start), (baseline, end))
        groups.append({'axis': axis, 'records': selected, 'start': world_point(frame, *a), 'end': world_point(frame, *b)})
    return groups, notes


def collect_plan(DB, document, view, elements, axes, mode, offset_mm, side, tick=lambda: None):
    frame = view_frame(DB, view)
    if not elements or len(elements) > MAX_ELEMENTS:
        raise ValueError('Choose between 1 and {} host elements in the view.'.format(MAX_ELEMENTS))
    records, notes, bounds, owners = [], [], [], []
    for element in elements:
        tick()
        owners.append(element.UniqueId)
        try:
            if not supported_category(DB, element):
                raise ValueError('Unsupported category; choose host building elements, casework, furniture or equipment.')
            found = geometry_records(DB, document, element, view, frame, axes)
            if not found:
                raise ValueError('No original planar face or straight grid reference parallel to the requested direction. Family centre/reference-plane guessing is excluded.')
            box = element_bounds(element, view, frame)
            records.extend(found)
            bounds.append(box)
        except Exception as error:
            notes.append('ID {}: {}'.format(id_value(element.Id), text_type(error)))
    if not bounds:
        raise ValueError('No usable geometry references.\n' + '\n'.join(notes[:12]))
    extent = [min(b[0] for b in bounds), min(b[1] for b in bounds), max(b[2] for b in bounds), max(b[3] for b in bounds)]
    groups, axis_notes = reference_groups(records, frame, extent, axes, mode, offset_mm, side)
    if not groups:
        raise ValueError('No dimension can be created.\n' + '\n'.join((notes + axis_notes)[:12]))
    return {'view': view.UniqueId, 'frame': frame, 'owners': owners, 'axes': list(axes), 'mode': mode,
            'offset_mm': float(offset_mm), 'side': side, 'groups': groups, 'notes': notes + axis_notes}


def collect_reference_plan(DB, document, view, references, axes, mode, offset_mm, side, tick=lambda: None):
    """Resolve picked faces/edges against original transformed geometry, never click-point guesses."""
    frame = view_frame(DB, view)
    if not 2 <= len(references) <= MAX_REFERENCES:
        raise ValueError('Pick between 2 and {} faces/straight edges, then click Finish.'.format(MAX_REFERENCES))
    records, notes, bounds, owners, picked, cache = [], [], [], [], [], {}
    for reference in references:
        tick()
        element = document.GetElement(reference.ElementId)
        if element is None or not supported_category(DB, element):
            raise ValueError('Pick references on supported host elements; linked content is excluded.')
        stable = reference.ConvertToStableRepresentation(document)
        picked.append({'owner': element.UniqueId, 'stable': stable})
        if element.UniqueId not in owners:
            owners.append(element.UniqueId)
        if element.UniqueId not in cache:
            cache[element.UniqueId] = geometry_records(DB, document, element, view, frame, axes, include_edges=True)
        matches = [row for row in cache[element.UniqueId]
                   if row['stable'] == stable or isinstance(element, DB.Grid)]
        if not matches:
            notes.append('ID {}: picked reference is not an original parallel planar face/straight edge in this view.'.format(id_value(element.Id)))
            continue
        records.extend(matches)
        bounds.append(element_bounds(element, view, frame))
    if not bounds:
        raise ValueError('No usable picked references.\n' + '\n'.join(notes[:12]))
    extent = [min(b[0] for b in bounds), min(b[1] for b in bounds), max(b[2] for b in bounds), max(b[3] for b in bounds)]
    groups, axis_notes = reference_groups(records, frame, extent, axes, mode, offset_mm, side)
    if not groups:
        raise ValueError('No dimension can be created from those references.\n' + '\n'.join((notes + axis_notes)[:12]))
    return {'view': view.UniqueId, 'frame': frame, 'owners': owners, 'axes': list(axes), 'mode': mode,
            'offset_mm': float(offset_mm), 'side': side, 'groups': groups, 'notes': notes + axis_notes,
            'selection_mode': 'References', 'picked': picked}


def reject_existing(DB, document, view, groups):
    def key(reference):
        owner = document.GetElement(reference.ElementId)
        # Revit can store a grid anchor as UID:0:LINEAR even when Reference(grid) is UID.
        return 'grid:' + owner.UniqueId if isinstance(owner, DB.Grid) else reference.ConvertToStableRepresentation(document)
    targets = set(tuple(sorted('grid:' + r['owner'] if isinstance(document.GetElement(r['owner']), DB.Grid)
                               else r['stable'] for r in group['records'])) for group in groups)
    # View collectors exclude clipped/hidden annotations; owner-view filtering still finds them.
    collector = DB.FilteredElementCollector(document).OfClass(DB.Dimension).WherePasses(DB.ElementOwnerViewFilter(view.Id))
    try:
        for dimension in collector:
            try:
                refs = tuple(sorted(key(r) for r in dimension.References))
            except Exception:
                continue
            if refs in targets:
                raise ValueError('An existing dimension already uses these references in this view (ID {}).'.format(id_value(dimension.Id)))
    finally:
        collector.Dispose()


def apply_plan(DB, document, plan):
    def prepare():
        view, viewport, style = (document.GetElement(plan[key]) for key in ('view', 'viewport', 'style'))
        if view is None or viewport is None or style is None or id_value(viewport.ViewId) != id_value(view.Id):
            raise ValueError('The sheet viewport, view or dimension style changed. Start again.')
        if not isinstance(style, DB.DimensionType) or style.StyleType != DB.DimensionStyleType.Linear:
            raise ValueError('Choose a linear dimension style.')
        elements = [document.GetElement(uid) for uid in plan['owners']]
        if any(element is None for element in elements):
            raise ValueError('An element was deleted after review. Start again.')
        if plan.get('selection_mode') == 'References':
            picked = []
            for target in plan['picked']:
                reference = DB.Reference.ParseFromStableRepresentation(document, target['stable'])
                owner = document.GetElement(target['owner'])
                if owner is None or id_value(reference.ElementId) != id_value(owner.Id):
                    raise ValueError('A picked reference no longer belongs to its reviewed element.')
                picked.append(reference)
            fresh = collect_reference_plan(DB, document, view, picked, plan['axes'], plan['mode'], plan['offset_mm'], plan['side'])
        else:
            fresh = collect_plan(DB, document, view, elements, plan['axes'], plan['mode'], plan['offset_mm'], plan['side'])
        if any(fresh[key] != plan[key] for key in ('frame', 'owners', 'groups', 'notes')):
            raise ValueError('Geometry, placement or view settings changed after review. Start again.')
        reject_existing(DB, document, view, plan['groups'])
        resolved = []
        for group in plan['groups']:
            references = DB.ReferenceArray()
            for record in group['records']:
                reference = DB.Reference.ParseFromStableRepresentation(document, record['stable'])
                owner = document.GetElement(record['owner'])
                if owner is None or id_value(reference.ElementId) != id_value(owner.Id):
                    raise ValueError('A geometry reference no longer belongs to its reviewed element.')
                references.Append(reference)
            resolved.append((group, references))
        return view, style, resolved

    def create(prepared):
        view, style, resolved = prepared
        dimensions = []
        for group, references in resolved:
            line = DB.Line.CreateBound(DB.XYZ(*group['start']), DB.XYZ(*group['end']))
            dimension = document.Create.NewDimension(view, line, references, style)
            if dimension is None:
                raise ValueError('Revit could not create the dimension. No dimensions were committed.')
            dimensions.append(dimension)
        document.Regenerate()
        for dimension in dimensions:
            parts = list(dimension.Segments) if dimension.NumberOfSegments else [dimension]
            if any(part.Value is None or part.Value <= EPS for part in parts):
                raise ValueError('Revit returned an invalid dimension measurement. The batch was rolled back.')
        return len(dimensions)
    return atomic_edit(DB, document, 'Hamdan - Add dimensions', prepare, create)
