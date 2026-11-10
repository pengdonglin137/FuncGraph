"""Wrap independent synthetic ring-buffer pages in a uftrace capture directory."""
import io
from pathlib import Path
import struct

from raw_fixture import container


def add_kernel(directory, unknown_entry=False, **options):
    payload = container(**options)
    stream = io.BytesIO(payload)
    stream.seek(12)
    endian = '>' if stream.read(1) == b'\x01' else '<'
    width = stream.read(1)[0]

    def number(layout):
        return struct.unpack(endian + layout, stream.read(struct.calcsize(layout)))[0]

    def string():
        value = bytearray()
        while True:
            character = stream.read(1)
            if character == b'\0':
                return bytes(value)
            if not character:
                raise ValueError('truncated synthetic fixture')
            value.extend(character)

    def sized(layout='Q'):
        return stream.read(number(layout))

    page_size = number('I')
    assert string() == b'header_page'
    formats = [('events/header_page', sized())]
    assert string() == b'header_event'
    sized()
    for unused in range(number('I')):
        value = sized()
        name = value.split(b'\n', 1)[0].split(b': ', 1)[1].decode()
        if unknown_entry and name in ('funcgraph_entry', 'fgraph_retaddr_entry'):
            value = value.replace(('name: ' + name + '\n').encode(), b'name: unknown_entry\n', 1)
            name = 'unknown_entry'
        formats.append(('events/ftrace/' + name + '/format', value))
    for unused in range(number('I')):
        system = string().decode()
        for unused_event in range(number('I')):
            value = sized()
            name = value.split(b'\n', 1)[0].split(b': ', 1)[1].decode()
            formats.append(('events/' + system + '/' + name + '/format', value))
    root = Path(directory)
    (root / 'kallsyms').write_bytes(sized('I'))
    header = f'PAGE_SIZE: {page_size}\nLONG_SIZE: {width}\nENDIAN: {"BE" if endian == ">" else "LE"}\n'.encode()
    for name, value in formats:
        header += f'TRACEFS: {name}: {len(value)}\n'.encode() + value
    (root / 'kernel_header').write_bytes(header)
    (root / 'kernel-cpu0.dat').write_bytes(payload[-page_size:])
    information = bytearray((root / 'info').read_bytes())
    host_endian = '>' if information[14] == 2 else '<'
    features = struct.unpack_from(host_endian + 'Q', information, 16)[0]
    struct.pack_into(host_endian + 'Q', information, 16, features | 4)
    (root / 'info').write_bytes(information)
