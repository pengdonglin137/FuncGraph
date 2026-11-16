"""Stream complete Text filter records and explicit source-pool identities."""

import hashlib
import json
import re
from html.parser import HTMLParser


FIELDS = ('cpu', 'pid', 'comm', 'ret', 'params', 'duration', 'func',
          'fold-id', 'fold-type', 'fold-ancestors', 'irqs', 'resched', 'hirq', 'preempt')
PRESENTATION_FIELDS = ('sort-duration', 'fold-ret', 'call-depth')


def is_trace_event(line):
    if line.get('record_type') is not None:
        return line.get('record_type') == 'event'
    if line.get('raw_func_name') or line.get('display_func_name'):
        return False
    raw = line.get('raw_line', '')
    if raw.lstrip().startswith('#') or re.search(r'\bLOST\b.*\bEVENTS\b', raw):
        return False
    return bool(re.search(r'\|\s*/\*\s*[A-Za-z_][\w.:-]*\s*:', raw) or
                re.search(r'(?:^\s*\d+\)|\|)\s*[^|]*-\d+\s+=>\s+[^|]*-\d+\s*$', raw))


def _dom_text(value):
    return value.replace('\r\n', '\n').replace('\r', '\n').replace('\0', '\ufffd')


class _LineText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def read(self, markup):
        self.reset()
        self.parts.clear()
        self.feed(_dom_text(markup))
        self.close()
        text = ''.join(self.parts)
        self.parts.clear()
        return text


class TextIndexWriter:
    def __init__(self, stream, include_space=False, include_presentation=False, include_raw_functions=False,
                 include_user_errors=False):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.byte_count = 0
        self.rows = 0
        self.events = []
        self.parser = _LineText()
        self.fields = FIELDS + ('space',) if include_space or include_presentation or include_raw_functions or include_user_errors else FIELDS
        if include_presentation or include_raw_functions or include_user_errors:
            self.fields += PRESENTATION_FIELDS
        if include_raw_functions or include_user_errors:
            self.fields += ('raw-func',)
        if include_user_errors:
            self.fields += ('user-error',)
        version = 6 if include_user_errors else 5 if include_raw_functions else 4 if include_presentation else 3 if include_space else 2
        self._write(f'{{"version":{version},"fields":' + json.dumps(self.fields, separators=(',', ':')) + ',"rows":[')

    def _write(self, value):
        payload = value.encode('utf-8')
        if self.stream.write(payload) != len(payload):
            raise OSError('short write while exporting Text filter index')
        self.digest.update(payload)
        self.byte_count += len(payload)

    def append(self, line_number, attributes, markup, source=None):
        if source is not None and (type(source) is not int or not 0 <= source < 2 ** 53):
            raise ValueError('invalid Text source ID')
        text = self.parser.read(markup)
        if attributes.get('trace-event') == '1':
            self.events.append(line_number)
        row = ([line_number, text] + [_dom_text(attributes[field]) if field in attributes else None for field in self.fields]
               + [source])
        self._write((',' if self.rows else '') + json.dumps(row, ensure_ascii=False, separators=(',', ':')))
        self.rows += 1

    def finish(self, filename):
        self._write(']' + (',"events":' + json.dumps(self.events, separators=(',', ':')) if self.events else '') + '}')
        return dict(file=filename, bytes=self.byte_count, sha256=self.digest.hexdigest(), format='json')
