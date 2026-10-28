# -*- coding: utf-8 -*-
"""Scan choices and scope helpers shared by Hamdan's audit command."""
import hashlib
import json
import time

PRESETS = {
    'Structure vs Mechanical / Plumbing': (
        ['OST_StructuralFraming', 'OST_StructuralColumns', 'OST_StructuralFoundation', 'OST_Floors'],
        ['OST_DuctCurves', 'OST_DuctFitting', 'OST_PipeCurves', 'OST_PipeFitting', 'OST_MechanicalEquipment']),
    'Architecture vs Mechanical / Plumbing': (
        ['OST_Walls', 'OST_Floors', 'OST_Ceilings', 'OST_Roofs'],
        ['OST_DuctCurves', 'OST_DuctFitting', 'OST_PipeCurves', 'OST_PipeFitting', 'OST_MechanicalEquipment']),
    'Structure vs Electrical': (
        ['OST_StructuralFraming', 'OST_StructuralColumns', 'OST_Floors'],
        ['OST_CableTray', 'OST_CableTrayFitting', 'OST_Conduit', 'OST_ConduitFitting', 'OST_ElectricalEquipment']),
    'Mechanical / Plumbing vs Electrical': (
        ['OST_DuctCurves', 'OST_DuctFitting', 'OST_PipeCurves', 'OST_PipeFitting'],
        ['OST_CableTray', 'OST_CableTrayFitting', 'OST_Conduit', 'OST_ConduitFitting'])
}


class ScanCancelled(BaseException):
    # Must pass through per-element `except Exception` handlers in audit checks.
    pass


class ScanProgress(object):
    def __init__(self, bar, clock=None):
        self.bar = bar
        self.clock = clock or time.time
        self.started = self.clock()
        self.last_update = -1
        self.label = 'Preparing scan'
        self.current = 0
        self.total = 1

    def pulse(self, label=None, current=None, total=None, force=False):
        if self.bar.cancelled:
            raise ScanCancelled()
        if label is not None:
            self.label = label
            force = True
        if current is not None:
            self.current = current
        if total is not None:
            self.total = max(1, total)
        now = self.clock()
        if force or now - self.last_update >= 0.2:
            elapsed = int(now - self.started)
            self.bar.title = '{} | elapsed {}m {:02d}s | {{value}}/{{max_value}}'.format(
                self.label, elapsed // 60, elapsed % 60)
            self.bar.update_progress(self.current, self.total)
            self.last_update = now
        if self.bar.cancelled:
            raise ScanCancelled()


def pair_key(first, second):
    return '|'.join(sorted([first, second]))


def pair_rules(config):
    """Combine hard and clearance settings for a category pair."""
    rules = {}
    for pair in config.get('hard_clash_categories', []):
        if len(pair) == 2:
            key = pair_key(*pair)
            rules.setdefault(key, {'categories': sorted(pair), 'hard': False, 'clearance_mm': None})['hard'] = True
    for key, tolerance in config.get('clearance_tolerances_mm', {}).items():
        categories = key.split('|')
        if len(categories) != 2:
            continue
        tolerance = float(tolerance)
        if tolerance < 0 or tolerance != tolerance or tolerance == float('inf'):
            raise ValueError('Clearance tolerances must be finite and zero or positive.')
        key = pair_key(*categories)
        rules.setdefault(key, {'categories': sorted(categories), 'hard': False, 'clearance_mm': None})['clearance_mm'] = tolerance
    return [rules[key] for key in sorted(rules)]


def preset_config(config, preset, clearance_mm):
    tolerance = float(clearance_mm)
    if tolerance < 0 or tolerance != tolerance or tolerance == float('inf'):
        raise ValueError('Enter a finite, non-negative clearance in millimetres.')
    left, right = PRESETS[preset]
    pairs = sorted(set(pair_key(a, b) for a in left for b in right))
    result = dict(config)
    result['hard_clash_categories'] = [key.split('|') for key in pairs]
    result['clearance_tolerances_mm'] = dict((key, tolerance) for key in pairs)
    return result


def scope_record(mode, config, preset='Configured pairs', level_id='', level_name='All levels', band=None):
    signature = hashlib.sha1(json.dumps(config, sort_keys=True, ensure_ascii=True).encode('utf-8')).hexdigest()
    return {'mode': mode, 'preset': preset, 'level_unique_id': level_id, 'level_name': level_name,
            'level_band_internal': band, 'config_signature': signature}


def in_level_band(box, band):
    if not band:
        return True
    low, high = band
    # Include elements crossing the level band, including vertical services.
    return box[5] >= low and (high is None or box[2] < high)
