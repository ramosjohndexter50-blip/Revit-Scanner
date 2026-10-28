# -*- coding: utf-8 -*-
"""Open the newest local Hamdan HTML dashboard."""
import os
from pyrevit import forms, revit
from System.Diagnostics import Process
from hamdan_session import current_report

report_dir = os.path.join(os.path.expanduser('~'), 'Documents', 'RevitScanner', 'reports')
data = (current_report(revit.doc)
        if revit.doc is not None and not revit.doc.IsFamilyDocument else None)
path = (data or {}).get('exports', {}).get('HTML dashboard')
if not path or not os.path.isfile(path):
    forms.alert('Run a new scan in this open model first. Previous reports are not restored automatically.', title='Open Dashboard', warn_icon=False)
else:
    # Revit's .NET host treats a direct HTML path as an executable. Explorer
    # uses the registered default browser association instead.
    Process.Start('explorer.exe', path)
