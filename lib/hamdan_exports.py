# -*- coding: utf-8 -*-
"""Dependency-free, Python 2/3 CSV and buildingSMART BCF 2.1 writers."""
from __future__ import unicode_literals
import csv
import io
import math
import os
import sys
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

try:
    text_type = unicode
except NameError:
    text_type = str


def csv_value(value):
    if isinstance(value, (int, float)):
        return value
    text = text_type('' if value is None else value)
    # Treat model text as text when the CSV is opened in a spreadsheet.
    if text.lstrip().startswith(('=', '+', '-', '@')):
        text = "'" + text
    return text.encode('utf-8') if sys.version_info[0] < 3 else text


def write_clash_csv(clashes, path, tick=lambda: None):
    fields = ['clash_id', 'kind', 'status', 'evidence', 'element_a_model', 'element_a_id',
              'element_a_category', 'element_a_name', 'element_a_level', 'element_b_model',
              'element_b_id', 'element_b_category', 'element_b_name', 'element_b_level',
              'x_m', 'y_m', 'z_m', 'tolerance_mm', 'message']
    handle = open(path, 'wb') if sys.version_info[0] < 3 else io.open(path, 'w', encoding='utf-8-sig', newline='')
    with handle:
        if sys.version_info[0] < 3:
            handle.write(b'\xef\xbb\xbf')
        writer = csv.writer(handle)
        writer.writerow(fields)
        for clash in clashes:
            tick()
            a, b = clash['element_a'], clash['element_b']
            point = clash.get('point_internal', [0, 0, 0])
            row = [clash['clash_id'], clash['kind'], clash.get('status', 'Active'), clash.get('evidence', ''),
                   a.get('model_name', ''), a.get('linked_element_id', a.get('id')), a.get('category_name', ''), a.get('display_name', a.get('name', '')),
                   a.get('level', ''), b.get('model_name', ''), b.get('linked_element_id', b.get('id')), b.get('category_name', ''),
                   b.get('display_name', b.get('name', '')), b.get('level', '')]
            row += [round(float(v) * 0.3048, 6) for v in point]
            row += [clash.get('tolerance_mm', 0), clash.get('message', '')]
            writer.writerow([csv_value(value) for value in row])


def guid(value):
    try:
        return str(uuid.UUID(text_type(value).strip('{}')))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, text_type(value).encode('utf-8')
                             if sys.version_info[0] < 3 else text_type(value)))


def child(parent, name, text=None, **attributes):
    node = ET.SubElement(parent, name, attributes)
    if text is not None:
        node.text = text_type(text)
    return node


def xml_bytes(root):
    # xml_declaration is unsupported by some IronPython ElementTree releases.
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding='utf-8')


def write_bcf21(clashes, path, project_name, tick=lambda: None, author='Hamdan'):
    """One independent topic and viewpoint per clash. Camera coordinates are host metres."""
    version = ET.Element('Version', {'VersionId': '2.1'})
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('bcf.version', xml_bytes(version))
        for index, clash in enumerate(clashes):
            tick()
            topic_id = guid(clash['clash_id'])
            view_id = guid(topic_id + ':viewpoint')
            root = ET.Element('Markup')
            header = child(root, 'Header')
            child(child(header, 'File', isExternal='true'), 'Filename', project_name + '.rvt')
            topic = child(root, 'Topic', Guid=topic_id, TopicType='Clash', TopicStatus=clash.get('status', 'Active'))
            child(topic, 'Title', clash.get('message', 'Clash')[:200])
            child(topic, 'Priority', 'Normal')
            child(topic, 'Index', index + 1)
            child(topic, 'CreationDate', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
            child(topic, 'CreationAuthor', author)
            refs = [clash.get('element_a', {}), clash.get('element_b', {})]
            description = clash.get('message', '') + '\n' + '\n'.join(
                '{} | link {} | element {} | {}'.format(r.get('model_name', project_name),
                    r.get('link_instance_unique_id', 'host'), r.get('linked_element_id', r.get('id', '')),
                    r.get('linked_unique_id', r.get('unique_id', ''))) for r in refs)
            child(topic, 'Description', description)
            child(child(root, 'Viewpoints', Guid=view_id), 'Viewpoint', 'viewpoint.bcfv')
            visualization = ET.Element('VisualizationInfo', {'Guid': view_id})
            components = child(visualization, 'Components')
            valid_refs = [r for r in refs if r.get('unique_id')]
            if valid_refs:
                selection = child(components, 'Selection')
                for ref in valid_refs:
                    # No invented IFC GUID: use Revit authoring identity; link path stays in Description.
                    component = child(selection, 'Component')
                    child(component, 'OriginatingSystem', 'Autodesk Revit')
                    child(component, 'AuthoringToolId', ref.get('linked_unique_id', ref['unique_id']))
            child(components, 'Visibility', DefaultVisibility='true')
            point = clash.get('point_internal')
            if not point:
                box = refs[0].get('bbox_internal') or [0, 0, 0, 0, 0, 0]
                point = [(box[i] + box[i + 3]) / 2.0 for i in range(3)]
            point_m = [float(v) * 0.3048 for v in point]
            extent = max([abs((r.get('bbox_internal') or [0] * 6)[i + 3] -
                             (r.get('bbox_internal') or [0] * 6)[i]) * 0.3048 for r in refs for i in range(3)] + [3.0])
            camera = child(visualization, 'PerspectiveCamera')
            for label, values in [('CameraViewPoint', [point_m[0], point_m[1], point_m[2] + extent * 2]),
                                  ('CameraDirection', [0, 0, -1]), ('CameraUpVector', [0, 1, 0])]:
                vector = child(camera, label)
                for axis, value in zip(('X', 'Y', 'Z'), values):
                    child(vector, axis, value)
            child(camera, 'FieldOfView', 60)
            package.writestr(topic_id + '/markup.bcf', xml_bytes(root))
            package.writestr(topic_id + '/viewpoint.bcfv', xml_bytes(visualization))
