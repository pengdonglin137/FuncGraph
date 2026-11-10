"""Publish immutable report resources and an atomically replaced HTML entry."""

from dataclasses import dataclass
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid

from .output import ReportSpool
from .textindex import TextIndexWriter


MARKER = b'FuncGraph sidecar report v1\n'


@dataclass(frozen=True)
class SidecarReport:
    path: str
    entry_path: str
    report_id: str
    byte_count: int
    single_file_bytes: int
    resource_bytes: int
    mode: str = 'sidecar'


def _blocks(spool, section):
    spool.file.seek(section.start)
    remaining = section.end - section.start
    while remaining:
        block = spool.file.read(min(remaining, 1024 * 1024))
        if not block:
            raise OSError('report spool ended before its recorded section boundary')
        yield block
        remaining -= len(block)


def _replace(blocks, before, after):
    pending = b''
    for block in blocks:
        pending += block
        boundary = max(0, len(pending) - len(before) + 1)
        position = 0
        while True:
            found = pending.find(before, position)
            if found < 0 or found >= boundary:
                break
            yield pending[position:found] + after
            position = found + len(before)
        consumed = max(position, boundary)
        if position < boundary:
            yield pending[position:boundary]
        pending = pending[consumed:]
    if pending:
        yield pending


def _copy(spool, section, destination, replacements=()):
    blocks = _blocks(spool, section)
    for before, after in replacements:
        blocks = _replace(blocks, before, after)
    for block in blocks:
        if destination.write(block) != len(block):
            raise OSError('short write while exporting report resources')


def _sync(stream):
    stream.flush()
    os.fsync(stream.fileno())


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _resource(spool, section_name, path, format):
    digest = hashlib.sha256()
    length = 0

    class ResourceWriter:
        def write(self, block):
            nonlocal length
            written = stream.write(block)
            if written != len(block):
                raise OSError('short write while exporting report resources')
            digest.update(block)
            length += written
            return written

    with path.open('xb') as stream:
        for section in spool.sections:
            if section.name == section_name:
                _copy(spool, section, ResourceWriter())
        _sync(stream)
    return dict(file=path.name, bytes=length, sha256=digest.hexdigest(), format=format)


def _target(path):
    if path.is_symlink():
        raise OSError('sidecar output must not be a symlink')
    if not path.exists():
        return False, None
    if not path.is_dir():
        raise OSError('sidecar output is not a directory: ' + str(path))
    if not any(path.iterdir()):
        return False, None
    marker = path / '.funcgraph-sidecar'
    if marker.is_symlink() or not marker.is_file():
        raise OSError('refusing to overwrite an unmanaged non-empty directory: ' + str(path))
    with marker.open('rb') as stream:
        if stream.read(len(MARKER) + 1) != MARKER:
            raise OSError('unrecognized sidecar report marker: ' + str(marker))
    entry = path / 'index.html'
    if entry.is_symlink() or not entry.is_file():
        raise OSError('managed sidecar output requires a regular index.html')
    return True, stat.S_IMODE(entry.stat().st_mode)


def write_sidecar(path, generator, byte_limit=None):
    if generator.timeline_json is None:
        raise ValueError('sidecar reports require --timeline')
    if byte_limit is not None and (isinstance(byte_limit, bool) or not isinstance(byte_limit, int) or byte_limit < 0):
        raise ValueError('sidecar threshold must be a nonnegative integer byte count')
    output = Path(os.path.abspath(os.fspath(path)))
    managed, previous_mode = _target(output)
    temporary = Path(tempfile.mkdtemp(prefix='.funcgraph-sidecar-', dir=output.parent))
    published_resources = None
    report_id = uuid.uuid4().hex
    version = 'data-' + report_id
    deferred = not generator.timeline_json.get('no_text')
    try:
        resources_directory = temporary / version
        resources_directory.mkdir()
        with ReportSpool(temporary / 'index.html') as spool:
            resources = {}
            index_path = resources_directory / 'text-index.json'
            with index_path.open('xb') if deferred else nullcontext() as index_stream:
                index = TextIndexWriter(index_stream) if deferred else None
                generator.text_index = index
                try:
                    spool.write_sections(generator.iter_sections())
                finally:
                    generator.text_index = None
                if index is not None:
                    resources['fgTextIndex'] = index.finish(index_path.name)
                    _sync(index_stream)
            spool.file.flush()
            external = byte_limit is None or spool.exceeds_threshold(byte_limit)
            if external:
                for name, filename in [('tlData', 'timeline.json'), ('cmpData', 'compare.json')]:
                    if any(section.name == 'view_data:' + name for section in spool.sections):
                        resources[name] = _resource(spool, 'view_data:' + name, resources_directory / filename, 'json')
                if deferred:
                    resources['fgText'] = _resource(spool, 'text', resources_directory / 'text.html', 'text')
                manifest = _json(dict(version=1, report_id=report_id, resources=resources,
                                      capabilities=dict(text='deferred-full' if deferred else 'omitted', sources='embedded',
                                                        text_filter='index-v1' if deferred else 'omitted')))
                with (resources_directory / 'manifest.json').open('xb') as stream:
                    stream.write(manifest)
                    _sync(stream)
                descriptor = _json(dict(version=1, report_id=report_id,
                                        manifest=dict(file=version + '/manifest.json', bytes=len(manifest),
                                                      sha256=hashlib.sha256(manifest).hexdigest(), format='json')))
                with (temporary / 'index.html').open('xb') as stream:
                    for section in spool.sections:
                        if section.name.startswith(('view_open:', 'view_data:', 'view_close:')):
                            continue
                        if deferred and section.name == 'text':
                            continue
                        if section.name == 'report_script':
                            stream.write(b'    <script type="application/json" id="fgDataManifest">' + descriptor + b'</script>\n')
                        if deferred and section.name == 'chrome':
                            _copy(spool, section, stream, (
                                (b'<div id="content">', b'<div id="content" data-fg-text="true" data-fg-text-index="true">'),
                                (b'data-default-view="text"', b'data-default-view="timeline" data-deferred-text="true"'),
                            ))
                        else:
                            _copy(spool, section, stream)
                    _sync(stream)
                resource_bytes = sum(resource['bytes'] for resource in resources.values()) + len(manifest)
            else:
                spool.publish()
                shutil.rmtree(resources_directory)
                resource_bytes = 0
            single_file_bytes = spool.byte_count
        with (temporary / '.funcgraph-sidecar').open('xb') as stream:
            stream.write(MARKER)
            _sync(stream)
        index = temporary / 'index.html'
        if previous_mode is not None:
            index.chmod(previous_mode)
        byte_count = index.stat().st_size + resource_bytes + len(MARKER)
        if managed:
            if external:
                destination = output / version
                os.replace(resources_directory, destination)
                published_resources = destination
            os.replace(index, output / 'index.html')
            published_resources = None
        else:
            os.replace(temporary, output)
            temporary = None
        return SidecarReport(os.fspath(path), str(output / 'index.html'), report_id,
                             byte_count, single_file_bytes, resource_bytes, 'sidecar' if external else 'single')
    finally:
        if published_resources is not None and (temporary / 'index.html').exists():
            shutil.rmtree(published_resources)
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
