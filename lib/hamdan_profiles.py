# -*- coding: utf-8 -*-
"""Validated, editable BEP rules with a per-model active profile."""
from __future__ import unicode_literals
import copy
import json
import os
import re
import math
from hamdan_audit import identity_key
from hamdan_config import DEFAULT_CONFIG
from hamdan_storage import read_json, update_json


def validate_rules(rules):
    if not isinstance(rules, dict):
        raise ValueError('Rules must be a JSON object.')
    unknown = set(rules) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError('Unknown rule keys: ' + ', '.join(sorted(unknown)))
    for key, value in rules.items():
        default = DEFAULT_CONFIG[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(key + ' must be true or false.')
        elif isinstance(default, dict) and not isinstance(value, dict):
            raise ValueError(key + ' must be an object.')
        elif isinstance(default, list) and not isinstance(value, list):
            raise ValueError(key + ' must be an array.')
        elif isinstance(default, (int, float)) and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or math.isnan(value) or math.isinf(value)):
            raise ValueError(key + ' must be a non-negative number.')
    for pattern in rules.get('naming_regex', {}).values():
        re.compile(pattern)
    re.compile(rules.get('annotation_view_name_regex', '.*'))
    for key in ('tag_required_categories', 'titleblock_required_parameters'):
        for value in rules.get(key, []):
            if not isinstance(value, type('')) and not isinstance(value, str):
                raise ValueError(key + ' must contain parameter/category names as text.')
            if not value.strip() or (key == 'tag_required_categories' and not value.startswith('OST_')):
                raise ValueError('Tag categories require OST_ names; title-block parameter names must not be blank.')
    for pair in rules.get('hard_clash_categories', []):
        if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(v, type('')) or isinstance(v, str) for v in pair):
            raise ValueError('Each hard clash pair must contain two category names.')
    for pair, value in rules.get('clearance_tolerances_mm', {}).items():
        if len(pair.split('|')) != 2 or not isinstance(value, (int, float)) or value < 0 or math.isnan(value) or math.isinf(value):
            raise ValueError('Clearance rules need CategoryA|CategoryB and a non-negative millimetre tolerance.')
    for key in ('health_weights', 'score_thresholds'):
        if any(not isinstance(v, (int, float)) or v < 0 or math.isnan(v) or math.isinf(v) for v in rules.get(key, {}).values()):
            raise ValueError(key + ' must contain non-negative numbers.')
    thresholds = rules.get('score_thresholds', {})
    if not 0 <= thresholds.get('warning', 60) <= thresholds.get('pass', 80) <= 100:
        raise ValueError('Score thresholds require 0 <= warning <= pass <= 100.')
    for category, required in rules.get('required_parameters', {}).items():
        if not category.startswith('OST_') or not isinstance(required, list):
            raise ValueError('Required parameters need OST_ category names and arrays of parameter rules.')
    return copy.deepcopy(rules)


def profiles_path(folder):
    return os.path.join(folder, 'bep_profiles.json')


def read_profiles(folder):
    data = read_json(profiles_path(folder), {'version': 1, 'profiles': {}, 'active_models': {}})
    if data.get('version') != 1 or not isinstance(data.get('profiles'), dict) or not isinstance(data.get('active_models'), dict):
        raise ValueError('Unsupported BEP profile file.')
    return data


def merge_rules(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        # A profile owns each supplied section; {} deliberately disables a section.
        result[key] = copy.deepcopy(value)
    return result


def effective_config(folder, identity):
    legacy = read_json(os.path.join(folder, 'audit_config.json'), {})
    config = merge_rules(DEFAULT_CONFIG, validate_rules(legacy))
    store = read_profiles(folder)
    name = store['active_models'].get(identity_key(identity), '')
    if name:
        if name not in store['profiles']:
            raise ValueError('The active BEP profile is missing: ' + name)
        config = merge_rules(config, validate_rules(store['profiles'][name]))
    config['bep_profile'] = name or 'Project rules (audit_config.json)'
    return config


def save_profile(folder, name, rules):
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError('Use a profile name between 1 and 80 characters.')
    rules = validate_rules(rules)
    def change(data):
        data['profiles'][name] = rules
    update_json(profiles_path(folder), change, {'version': 1, 'profiles': {}, 'active_models': {}})


def activate_profile(folder, identity, name):
    def change(data):
        if name and name not in data['profiles']:
            raise ValueError('Profile not found.')
        data['active_models'][identity_key(identity)] = name
    update_json(profiles_path(folder), change, {'version': 1, 'profiles': {}, 'active_models': {}})
