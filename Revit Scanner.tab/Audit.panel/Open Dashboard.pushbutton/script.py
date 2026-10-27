# -*- coding: utf-8 -*-
"""Open the newest local Revit Scanner HTML dashboard."""
import os
from pyrevit import forms
from System.Diagnostics import Process

report_dir = os.path.join(os.path.expanduser('~'), 'Documents', 'RevitScanner', 'reports')
dashboards = []
if os.path.isdir(report_dir):
    dashboards = [os.path.join(report_dir, item) for item in os.listdir(report_dir)
                  if item.startswith('audit_') and item.endswith('.html')]
if not dashboards:
    forms.alert('No HTML dashboard exists yet. Run Audit first.', title='Open Dashboard')
else:
    Process.Start(max(dashboards, key=os.path.getmtime))
