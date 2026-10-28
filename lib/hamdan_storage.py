# -*- coding: utf-8 -*-
"""Local JSON storage with atomic replacement and explicit concurrent-write rejection."""
from __future__ import unicode_literals
import io
import json
import os
import uuid
from datetime import datetime


def timestamp():
    return datetime.now().astimezone().isoformat() if hasattr(os, 'replace') else datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with io.open(path, 'r', encoding='utf-8') as stream:
        return json.load(stream)  # Never silently overwrite malformed user data.


def atomic_json(path, data):
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    temp = path + '.' + uuid.uuid4().hex + '.tmp'
    try:
        with io.open(temp, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(data, indent=2, ensure_ascii=True))
            stream.flush()
        if hasattr(os, 'replace'):
            os.replace(temp, path)
        else:
            from System.IO import File
            if File.Exists(path):
                File.Replace(temp, path, None)
            else:
                File.Move(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def update_json(path, change, default=None):
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    lock = path + '.lock'
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        raise ValueError('This register is being edited by another command. Try again after it finishes: ' + path)
    try:
        data = read_json(path, default)
        result = change(data)
        atomic_json(path, data)
        return result
    finally:
        os.close(handle)
        os.remove(lock)
