"""btf.py: type classification from a synthetic BTF blob, ELF extraction, split BTF."""
import os
import struct
import tempfile
import unittest

import helpers  # noqa: F401
from fglib import btf


class BtfBuilder:
    """Tiny BTF writer: enough kinds for return-type classification."""

    def __init__(self, base=None):
        self.types = b''
        self.strs = b'\0'
        self.base_next = base.next_id if base else 1
        self.base_str = base.str_end if base else 0
        self.n = 0

    def s(self, name):
        off = self.base_str + len(self.strs)
        self.strs += name.encode() + b'\0'
        return off

    def add(self, name, kind, vlen, size_type, extra=b''):
        self.types += struct.pack('<III', self.s(name) if name else 0, (kind << 24) | vlen, size_type) + extra
        self.n += 1
        return self.base_next + self.n - 1

    def int_(self, name, size, signed=False, boolean=False):
        enc = (1 if signed else 0) | (4 if boolean else 0)
        return self.add(name, btf.KIND_INT, 0, size, struct.pack('<I', (enc << 24) | (size * 8)))

    def func(self, name, ret_tid):
        proto = self.add(None, btf.KIND_FUNC_PROTO, 0, ret_tid)
        return self.add(name, btf.KIND_FUNC, 0, proto)

    def blob(self):
        hdr = struct.pack('<HBBIIIII', btf.BTF_MAGIC, 1, 0, 24, 0, len(self.types), len(self.types), len(self.strs))
        return hdr + self.types + self.strs


def make_elf(sections):
    """Minimal little-endian ELF64 with the given {name: bytes} sections."""
    shstr = b'\0' + b''.join(n.encode() + b'\0' for n in sections) + b'.shstrtab\0'
    body, offs, data = b'', [], 64
    for content in sections.values():
        offs.append(data + len(body))
        body += content
    shstr_off = data + len(body)
    body += shstr
    shoff = data + len(body)
    shnum = len(sections) + 2
    ehdr = b'\x7fELF' + bytes([2, 1, 1]) + b'\0' * 9
    ehdr += struct.pack('<HHIQQQIHHHHHH', 1, 183, 1, 0, 0, shoff, 0, 64, 0, 0, 64, shnum, shnum - 1)
    sh = b'\0' * 64
    name_off = 1
    for (name, content), off in zip(sections.items(), offs):
        sh += struct.pack('<IIQQQQIIQQ', name_off, 1, 0, 0, off, len(content), 0, 0, 1, 0)
        name_off += len(name) + 1
    sh += struct.pack('<IIQQQQIIQQ', name_off, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0)
    return ehdr + body + sh


def base_blob():
    b = BtfBuilder()
    t_int = b.int_('int', 4, signed=True)
    t_long = b.int_('long', 8, signed=True)
    t_uint = b.int_('unsigned int', 4)
    t_bool = b.int_('_Bool', 1, boolean=True)
    t_ptr = b.add(None, btf.KIND_PTR, 0, t_int)
    t_const = b.add(None, btf.KIND_CONST, 0, t_ptr)
    t_td = b.add('ssize_t', btf.KIND_TYPEDEF, 0, t_long)
    b.func('ret_void', 0)
    b.func('ret_int', t_int)
    b.func('ret_ssize', t_td)
    b.func('ret_uint', t_uint)
    b.func('ret_bool', t_bool)
    b.func('ret_ptr', t_const)
    return b


class BtfTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fgbtf_')

    def write(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, 'wb') as f:
            f.write(data)
        return p

    def test_classification_raw_blob(self):
        info = btf.BtfInfo(base_blob().blob())
        want = {'ret_void': ('void', 0), 'ret_int': ('sint', 4), 'ret_ssize': ('sint', 8),
                'ret_uint': ('uint', 4), 'ret_bool': ('bool', 1), 'ret_ptr': ('ptr', 8)}
        for fn, cls in want.items():
            self.assertEqual(info.func_ret[fn], cls, fn)

    def test_elf_section_and_db(self):
        vml = self.write('vmlinux', make_elf({'.text': b'\0' * 16, '.BTF': base_blob().blob()}))
        db = btf.build_return_db(vml)
        self.assertEqual(len(db), 6)
        self.assertEqual(db.lookup('ret_int'), (('sint', 4), False))
        self.assertEqual(db.lookup('ret_int.isra.0'), (('sint', 4), True))
        self.assertEqual(db.lookup('nope'), (None, False))

    def test_elf_without_btf(self):
        vml = self.write('vmlinux', make_elf({'.text': b'\0' * 16}))
        self.assertIsNone(btf.load_btf(vml))
        self.assertEqual(len(btf.build_return_db(vml)), 0)

    def test_split_module_btf(self):
        base = btf.BtfInfo(base_blob().blob())
        m = BtfBuilder(base)
        m.func('mod_ret_int', 1)          # type id 1 = 'int' from the base
        ko = self.write('mod.ko', make_elf({'.BTF': m.blob()}))
        vml = self.write('vmlinux', make_elf({'.BTF': base_blob().blob()}))
        db = btf.build_return_db(vml, module_paths=[ko])
        self.assertEqual(db.lookup('mod_ret_int')[0], ('sint', 4))
        self.assertEqual(db.lookup('ret_void')[0], ('void', 0))

    def test_raw_blob_as_extra_base(self):
        raw = self.write('vmlinux.btf', base_blob().blob())
        no = self.write('vmlinux', make_elf({'.text': b'\0'}))
        db = btf.build_return_db(no, extra_paths=[raw])
        self.assertEqual(db.lookup('ret_ptr')[0], ('ptr', 8))

    def test_bad_magic(self):
        with self.assertRaises(btf.BtfError):
            btf.BtfInfo(b'\0' * 32)

    @unittest.skipUnless(os.environ.get('FG_VMLINUX'), 'set FG_VMLINUX to a vmlinux with .BTF')
    def test_real_vmlinux(self):
        db = btf.build_return_db(os.environ['FG_VMLINUX'])
        self.assertGreater(len(db), 1000)
        self.assertEqual(db.lookup('mutex_unlock')[0][0], 'void')


if __name__ == '__main__':
    unittest.main()
