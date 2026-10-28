# -*- coding: utf-8 -*-
"""Portable HTML/JSON/CSV reports for document checks and the issue register."""
from __future__ import unicode_literals
import csv
import io
import json
import os
import sys
from datetime import datetime
from xml.sax.saxutils import escape
from hamdan_exports import csv_value
from hamdan_elements import text_type
from hamdan_storage import atomic_json


def table(headers, rows):
    def cell(value):
        return escape(text_type('' if value is None else value), {'"': '&quot;', "'": '&#39;'})
    return '<table><thead><tr>' + ''.join('<th>' + cell(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join(
        '<tr>' + ''.join('<td>' + cell(v) + '</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table>'


def write_check_report(folder, title, model, data):
    if not os.path.isdir(folder):
        os.makedirs(folder)
    base = os.path.join(folder, 'check_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    data = dict(data, title=title, model_name=model)
    data['exports'] = {'HTML dashboard': base + '.html', 'JSON results': base + '.json', 'CSV findings': base + '.csv'}
    atomic_json(base + '.json', data)
    rows = data.get('findings', [])
    fields = ['type', 'severity', 'message', 'element_ids', 'view_name', 'sheet_name']
    handle = open(base + '.csv', 'wb') if sys.version_info[0] < 3 else io.open(base + '.csv', 'w', encoding='utf-8-sig', newline='')
    with handle:
        if sys.version_info[0] < 3:
            handle.write(b'\xef\xbb\xbf')
        writer = csv.writer(handle)
        writer.writerow(fields)
        for row in rows:
            writer.writerow([csv_value(', '.join(text_type(v) for v in row[key]) if key == 'element_ids' else row.get(key, '')) for key in fields])
    content = table(['Severity', 'Type', 'Finding', 'Element IDs', 'View / Sheet'], [
        [r.get('severity'), r.get('type'), r.get('message'), ', '.join(text_type(v) for v in r.get('element_ids', [])), r.get('view_name') or r.get('sheet_name', '')] for r in rows])
    coverage_rows = [(key.replace('_', ' ').capitalize(), ('Complete' if value else 'Incomplete') if key == 'complete' else value)
                     for key, value in sorted(data.get('coverage', {}).items())]
    html = '<!doctype html><html><head><meta charset="utf-8"><title>Hamdan</title><style>body{font:14px Segoe UI,Arial;background:#f3f6fa;color:#13253e;margin:32px}section{background:white;padding:24px;margin-bottom:18px;border-radius:12px}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}th{cursor:pointer;background:#e8f1ff}input{padding:10px;width:90%}</style></head><body><h1>Hamdan - ' + escape(title) + '</h1><p>' + escape(model) + '</p><section><h2>Coverage</h2>' + table(['Metric', 'Result'], coverage_rows) + '<p>' + escape(data.get('scope_note', '')) + '</p></section><section><h2>Findings (' + str(len(rows)) + ')</h2><input id="search" placeholder="Search findings" aria-label="Search findings"/>' + content + '</section><section><h2>Skipped / inaccessible</h2>' + table(['Type', 'Element', 'Reason'], [[r.get('type'), r.get('element_id'), r.get('message')] for r in data.get('notes', [])]) + '</section><p><a href="' + os.path.basename(base) + '.json">JSON</a> | <a href="' + os.path.basename(base) + '.csv">CSV</a></p><script>document.querySelectorAll("th").forEach(function(h){h.onclick=function(){var t=h.closest("table"),i=Array.from(h.parentNode.children).indexOf(h);h.asc=!h.asc;Array.from(t.tBodies[0].rows).sort(function(a,b){return a.cells[i].textContent.localeCompare(b.cells[i].textContent,undefined,{numeric:true})*(h.asc?1:-1)}).forEach(function(r){t.tBodies[0].appendChild(r)})}});document.getElementById("search").oninput=function(){var q=this.value.toLowerCase();document.querySelectorAll("section:nth-of-type(2) tbody tr").forEach(function(r){r.hidden=r.textContent.toLowerCase().indexOf(q)<0})};</script></body></html>'
    with io.open(base + '.html', 'w', encoding='utf-8') as stream:
        stream.write(html)
    return base + '.html'
