# -*- coding: utf-8 -*-
"""Open the newest local Hamdan HTML dashboard."""
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
    # Revit's .NET host treats a direct HTML path as an executable. Explorer
    # uses the registered default browser association instead.
    Process.Start('explorer.exe', max(dashboards, key=os.path.getmtime))
