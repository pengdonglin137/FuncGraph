"""Offline validation: independently decode saved ring pages, not a drained trace file."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re


def decode(directory):
    directory = Path(directory)
    header = (directory / 'kernel_header').read_bytes()
    page_size = int(re.search(rb'^PAGE_SIZE: (\d+)', header, re.M)[1])
    width = int(re.search(rb'^LONG_SIZE: (\d+)', header, re.M)[1])
    endian = '>' if re.search(rb'^ENDIAN: (\w+)', header, re.M)[1] == b'BE' else '<'
    formats = {}
    position = 0
    page_header = None
    while True:
        match = re.search(rb'^TRACEFS: ([^\n:]+): (\d+)\n', header[position:], re.M)
        if match is None:
            break
        position += match.end()
        content = header[position:position + int(match[2])]
        position += len(content)
        path = match[1].decode()
        fields = {}
        for field in re.finditer(rb'field:(.*?);\s*offset:(\d+);\s*size:(\d+);', content):
            name = re.search(rb'(\w+)(?:\[[^\]]*\])?\s*$', field[1])[1].decode()
            fields[name] = (int(field[2]), int(field[3]), field[1])
        if path == 'events/header_page':
            page_header = fields
            continue
        identifier = int(re.search(rb'^ID: (\d+)', content, re.M)[1])
        formats[identifier] = (path.split('/')[1:3], fields)
    if page_header is None:
        raise ValueError('missing saved ring page format')

    def integer(data, offset, size):
        if size not in (1, 2, 4, 8) or offset < 0 or offset + size > len(data):
            raise ValueError('invalid recorded field bounds')
        return int.from_bytes(data[offset:offset + size], 'big' if endian == '>' else 'little')

    for path in sorted(directory.glob('kernel-cpu*.dat')):
        cpu = int(re.search(r'cpu(\d+)\.dat$', path.name)[1])
        raw = path.read_bytes()
        if len(raw) % page_size:
            raise ValueError('truncated CPU page: ' + path.name)
        for page_offset in range(0, len(raw), page_size):
            page = raw[page_offset:page_offset + page_size]
            timestamp = integer(page, *page_header['timestamp'][:2])
            commit = integer(page, *page_header['commit'][:2])
            offset = page_header['data'][0]
            end = offset + (commit & ((1 << 30) - 1))
            if end > page_size:
                raise ValueError('invalid page commit length')
            missed = None
            if commit & (1 << 31):
                missed = integer(page, end, width) if commit & (1 << 30) else -1
            first = True
            while offset < end:
                word = integer(page, offset, 4)
                kind = word >> 27 if endian == '>' else word & 31
                delta = word & ((1 << 27) - 1) if endian == '>' else word >> 5
                if kind == 29:
                    offset += 4 + integer(page, offset + 4, 4)
                    continue
                if kind in (30, 31):
                    value = (integer(page, offset + 4, 4) << 27) | delta
                    timestamp = value if kind == 31 else timestamp + value
                    offset += 8
                    continue
                timestamp += delta
                length = kind * 4 if kind else integer(page, offset + 4, 4) - 4
                start = offset + (4 if kind else 8)
                if length < 0 or start + length > end:
                    raise ValueError('invalid ring event length')
                data = page[start:start + length]
                offset = start + length
                system_name, fields = formats[integer(data, 0, 2)]
                system, name = system_name

                def number(field):
                    return integer(data, *fields[field][:2])

                if first and missed is not None:
                    yield {'type': 'lost', 'space': 'kernel', 'time_ns': timestamp,
                           'tid': None, 'cpu': cpu, 'count': missed}
                first = False
                record = {'type': 'event', 'space': 'kernel', 'time_ns': timestamp,
                          'tid': number('common_pid'), 'cpu': cpu,
                          'common_flags': number('common_flags'),
                          'common_preempt_count': number('common_preempt_count')}
                if record['tid'] & (1 << 31):
                    record['tid'] -= 1 << 32
                if system != 'ftrace' or name not in ('funcgraph_entry', 'fgraph_retaddr_entry', 'funcgraph_exit'):
                    record['name'] = system + ':' + name
                    yield record
                    continue
                record.update(type='exit' if name == 'funcgraph_exit' else 'entry',
                              address=hex(number('func')), depth=number('depth'))
                for field, exported in (('retaddr', 'retaddr'), ('retval', 'retval'),
                                        ('calltime', 'calltime_ns'), ('rettime', 'rettime_ns'),
                                        ('overrun', 'overrun')):
                    if field in fields:
                        value = number(field)
                        record[exported] = hex(value) if field in ('retaddr', 'retval') else value
                if 'args' in fields:
                    argument_offset, size, declaration = fields['args']
                    length = size or len(data) - argument_offset
                    if b'__data_loc' in declaration or b'__rel_loc' in declaration:
                        location = number('args')
                        length = location >> 16
                        argument_offset = location & 65535
                        if b'__rel_loc' in declaration:
                            argument_offset += fields['args'][0] + size
                    if length % width or argument_offset + length > len(data):
                        raise ValueError('invalid argument array bounds')
                    record['args'] = [hex(integer(data, argument_offset + index, width))
                                      for index in range(0, length, width)]
                yield record


def verify(directory, exported):
    expected = collections.defaultdict(list)
    for record in decode(directory):
        expected[record['cpu']].append(record)
    actual = collections.defaultdict(list)
    with open(exported) as stream:
        for line in stream:
            record = json.loads(line)
            if record.get('space') == 'kernel':
                actual[record['cpu']].append(record)
    if set(expected) != set(actual):
        raise AssertionError('CPU stream inventory differs')
    counts = collections.Counter()
    for cpu, records in expected.items():
        if len(records) != len(actual[cpu]):
            raise AssertionError(f'CPU {cpu}: expected {len(records)}, got {len(actual[cpu])}')
        for index, (reference, observed) in enumerate(zip(records, actual[cpu])):
            for key, value in reference.items():
                if observed.get(key) != value:
                    raise AssertionError(f'CPU {cpu} record {index} {key}: {value!r} != {observed.get(key)!r}')
            counts[reference['type']] += 1
    return {'kernel_records': sum(counts.values()), 'types': dict(counts),
            'export_sha256': hashlib.sha256(Path(exported).read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('jsonl')
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.capture, arguments.jsonl), indent=2))
