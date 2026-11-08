#!/usr/bin/env python3
"""Minimal BTF reader (standard library only).

Extracts the return-type class of every kernel function from BTF so that
funcgraph-retval values can be interpreted correctly:

    void  -> the printed value is leftover register content, never an error
    ptr   -> ERR_PTR() range means error, 0 means NULL
    sint  -> negative errno range means error (truncated to the type width)
    uint / bool / enum / other -> not treated as errors

Sources accepted:
    * ELF files containing a .BTF section (vmlinux, *.ko, optionally .gz/.xz/.zst-less)
    * raw BTF blobs (e.g. a copy of /sys/kernel/btf/vmlinux or /sys/kernel/btf/<module>)

Module BTF is "split" BTF: its type ids and string offsets continue those of
vmlinux, so a base BtfInfo must be supplied when loading it.
"""

import gzip
import lzma
import os
import struct
import zlib

BTF_MAGIC = 0xEB9F

KIND_INT, KIND_PTR, KIND_ARRAY, KIND_STRUCT, KIND_UNION, KIND_ENUM = 1, 2, 3, 4, 5, 6
KIND_FWD, KIND_TYPEDEF, KIND_VOLATILE, KIND_CONST, KIND_RESTRICT = 7, 8, 9, 10, 11
KIND_FUNC, KIND_FUNC_PROTO, KIND_VAR, KIND_DATASEC, KIND_FLOAT = 12, 13, 14, 15, 16
KIND_DECL_TAG, KIND_TYPE_TAG, KIND_ENUM64 = 17, 18, 19

# Extra bytes that follow struct btf_type for each kind: (fixed, per_vlen)
_KIND_EXTRA = {
    KIND_INT: (4, 0), KIND_ARRAY: (12, 0), KIND_STRUCT: (0, 12), KIND_UNION: (0, 12),
    KIND_ENUM: (0, 8), KIND_FUNC_PROTO: (0, 8), KIND_VAR: (4, 0), KIND_DATASEC: (0, 12),
    KIND_DECL_TAG: (4, 0), KIND_ENUM64: (0, 12),
}
_MODIFIERS = (KIND_TYPEDEF, KIND_VOLATILE, KIND_CONST, KIND_RESTRICT, KIND_TYPE_TAG)

SHF_COMPRESSED = 0x800


class BtfError(Exception):
    pass


# ------------------------------------------------------------------
# ELF section extraction
# ------------------------------------------------------------------

def _read_file(path):
    with open(path, 'rb') as f:
        data = f.read()
    if data[:2] == b'\x1f\x8b':
        return gzip.decompress(data)
    if data[:6] == b'\xfd7zXZ\x00':
        return lzma.decompress(data)
    if data[:4] == b'\x28\xb5\x2f\xfd':
        raise BtfError(f"{path}: zstd-compressed files are not supported, decompress first")
    return data


def elf_section(data, name):
    """Return the contents of section `name` from an ELF image, or None."""
    if data[:4] != b'\x7fELF':
        return None
    is64 = data[4] == 2
    end = '<' if data[5] == 1 else '>'
    if is64:
        shoff, = struct.unpack_from(end + 'Q', data, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from(end + 'HHH', data, 0x3A)
        shfmt = end + 'IIQQQQIIQQ'
    else:
        shoff, = struct.unpack_from(end + 'I', data, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from(end + 'HHH', data, 0x2E)
        shfmt = end + 'IIIIIIIIII'
    if shoff == 0 or shnum == 0:
        return None

    def sh(i):
        f = struct.unpack_from(shfmt, data, shoff + i * shentsize)
        # name, type, flags, addr, offset, size
        return f[0], f[1], f[2], f[3], f[4], f[5]

    if shnum == 0 or shstrndx >= shnum:
        return None
    _, _, _, _, str_off, str_size = sh(shstrndx)
    target = name.encode()
    for i in range(shnum):
        n_off, s_type, flags, _, off, size = sh(i)
        p = str_off + n_off
        if data[p:p + len(target) + 1] != target + b'\x00':
            continue
        if s_type == 8:  # SHT_NOBITS
            return None
        raw = data[off:off + size]
        if flags & SHF_COMPRESSED:
            if is64:
                ch_type, = struct.unpack_from(end + 'I', raw, 0)
                hdr = 24
            else:
                ch_type, = struct.unpack_from(end + 'I', raw, 0)
                hdr = 12
            if ch_type != 1:
                raise BtfError(f"section {name}: unsupported compression type {ch_type}")
            raw = zlib.decompress(raw[hdr:])
        return raw
    return None


# ------------------------------------------------------------------
# BTF parsing
# ------------------------------------------------------------------

class BtfInfo:
    """Parsed BTF: type table + function return classes.

    Attributes:
        func_ret: dict  func_name -> (cls, size_bytes)
                  cls in {'void','ptr','sint','uint','bool','enum','other'}
    """

    def __init__(self, blob, base=None, source=''):
        self.source = source
        self.base = base
        if len(blob) < 24:
            raise BtfError(f"{source}: BTF blob too small")
        magic_le, = struct.unpack_from('<H', blob, 0)
        if magic_le == BTF_MAGIC:
            self.end = '<'
        elif struct.unpack_from('>H', blob, 0)[0] == BTF_MAGIC:
            self.end = '>'
        else:
            raise BtfError(f"{source}: bad BTF magic")
        e = self.end
        _, _, _, hdr_len, type_off, type_len, str_off, str_len = struct.unpack_from(e + 'HBBIIIII', blob, 0)
        self._types_raw = blob[hdr_len + type_off: hdr_len + type_off + type_len]
        self._strs = blob[hdr_len + str_off: hdr_len + str_off + str_len]
        self.start_id = (base.next_id if base else 1)
        self.str_base = (base.str_end if base else 0)
        self.str_end = self.str_base + len(self._strs)
        # kinds[i], info fields per local index (id - start_id)
        self.kinds = []
        self.names = []
        self.sizetype = []
        self.extra = []      # INT encoding / ENUM signedness
        self.vlens = []
        self._parse_types()
        self.next_id = self.start_id + len(self.kinds)
        self.func_ret = {}
        self._collect_functions()

    # -- strings ---------------------------------------------------
    def string(self, off):
        if off < self.str_base:
            return self.base.string(off) if self.base else ''
        off -= self.str_base
        end = self._strs.find(b'\x00', off)
        if end < 0:
            end = len(self._strs)
        return self._strs[off:end].decode('utf-8', 'replace')

    # -- types -----------------------------------------------------
    def _parse_types(self):
        e = self.end
        raw = self._types_raw
        pos = 0
        n = len(raw)
        unpack = struct.unpack_from
        while pos + 12 <= n:
            name_off, info, size_type = unpack(e + 'III', raw, pos)
            kind = (info >> 24) & 0x1f
            vlen = info & 0xffff
            kflag = info >> 31
            pos += 12
            fixed, per = _KIND_EXTRA.get(kind, (0, 0))
            extra = 0
            if kind == KIND_INT:
                extra, = unpack(e + 'I', raw, pos)
            elif kind in (KIND_ENUM, KIND_ENUM64):
                extra = kflag
            pos += fixed + per * vlen
            self.kinds.append(kind)
            self.names.append(name_off)
            self.sizetype.append(size_type)
            self.extra.append(extra)
            self.vlens.append(vlen)

    def _get(self, tid):
        """Return (kind, size_type, extra) for type id, walking into base if needed."""
        if tid < self.start_id:
            return self.base._get(tid) if self.base else (None, 0, 0)
        i = tid - self.start_id
        if i >= len(self.kinds):
            return (None, 0, 0)
        return self.kinds[i], self.sizetype[i], self.extra[i]

    def classify(self, tid, _depth=0):
        """Classify a type id into (cls, size)."""
        if tid == 0:
            return ('void', 0)
        if _depth > 32:
            return ('other', 0)
        kind, st, extra = self._get(tid)
        if kind in _MODIFIERS:
            return self.classify(st, _depth + 1)
        if kind == KIND_PTR:
            return ('ptr', 8)
        if kind == KIND_INT:
            enc = (extra >> 24) & 0xf
            if enc & 4:
                return ('bool', st)
            if enc & 1:
                return ('sint', st)
            return ('uint', st)
        if kind in (KIND_ENUM, KIND_ENUM64):
            return ('sint' if extra else 'enum', st)
        return ('other', st if kind in (KIND_STRUCT, KIND_UNION) else 0)

    def _collect_functions(self):
        for i, kind in enumerate(self.kinds):
            if kind != KIND_FUNC:
                continue
            name = self.string(self.names[i])
            proto = self.sizetype[i]
            pk, ret_tid, _ = self._get(proto)
            if pk != KIND_FUNC_PROTO:
                continue
            self.func_ret[name] = self.classify(ret_tid)

    def lookup(self, func_name):
        """Return (cls, size) for a function, trying compiler-suffix stripping."""
        r = self.func_ret.get(func_name)
        if r is None and self.base is not None:
            r = self.base.lookup(func_name)
        return r


def load_btf(path, base=None):
    """Load BTF from an ELF (.BTF section) or a raw BTF blob. Returns BtfInfo or None."""
    data = _read_file(path)
    blob = None
    if data[:4] == b'\x7fELF':
        blob = elf_section(data, '.BTF')
        if blob is None:
            return None
    else:
        blob = data
    return BtfInfo(blob, base=base, source=path)


class ReturnTypeDB:
    """Aggregate function return classes from vmlinux and module BTF."""

    def __init__(self):
        self.funcs = {}
        self.sources = []
        self.errors = []

    def add(self, info):
        if info is None:
            return
        # Module (split) BTF only contributes its own functions
        for k, v in info.func_ret.items():
            self.funcs.setdefault(k, v)
        self.sources.append(info.source)

    def __len__(self):
        return len(self.funcs)

    def lookup(self, func_name):
        """Look up a function, stripping GCC/LLVM clone suffixes (.isra.0, .constprop.0, .llvm.123)."""
        r = self.funcs.get(func_name)
        if r is not None:
            return r, False
        base = func_name.split('.', 1)[0]
        if base != func_name:
            r = self.funcs.get(base)
            if r is not None:
                # IPA-SRA clones may drop the return value; mark as "from clone"
                return r, True
        return None, False


def build_return_db(vmlinux=None, extra_paths=None, module_paths=None, verbose=False):
    """Build a ReturnTypeDB from vmlinux + optional extra BTF/ELF files + module .ko files.

    extra_paths: raw BTF or ELF files. A raw blob named 'vmlinux' (e.g. a copy of
                 /sys/kernel/btf/vmlinux) is used as base when vmlinux has no .BTF.
    module_paths: list of .ko paths (split BTF on top of the base).
    """
    db = ReturnTypeDB()
    base = None
    candidates = []
    if vmlinux:
        candidates.append(vmlinux)
    extra_paths = list(extra_paths or [])
    # Base: first loadable non-split source
    for p in candidates + extra_paths:
        if base is not None:
            break
        try:
            info = load_btf(p)
        except (OSError, BtfError, struct.error, EOFError, lzma.LZMAError, zlib.error) as e:
            db.errors.append(f"{p}: {e}")
            continue
        if info is not None and info.kinds:
            base = info
            db.add(info)
    for p in extra_paths + list(module_paths or []):
        if base is not None and p == base.source:
            continue
        if p in extra_paths and os.path.basename(p).startswith('vmlinux'):
            continue  # another base candidate, not split BTF
        try:
            info = load_btf(p, base=base)
        except (OSError, BtfError, struct.error, EOFError, lzma.LZMAError, zlib.error) as e:
            db.errors.append(f"{p}: {e}")
            continue
        db.add(info)
    return db


if __name__ == '__main__':
    import sys
    import time
    t = time.time()
    db = build_return_db(sys.argv[1], module_paths=sys.argv[2:])
    print(f"functions={len(db)} sources={db.sources} errors={db.errors} time={time.time()-t:.2f}s")
    for fn in ('do_raw_spin_unlock', 'search_cmp_ftr_reg', 'step_into', 'walk_component',
               'link_path_walk', '__arm64_sys_newfstatat', 'kmalloc_trace', 'kmem_cache_alloc_noprof',
               'mutex_unlock', 'rcu_read_lock_any_held', 'finish_task_switch.isra.0', 'ktime_get'):
        print(f"  {fn:32s} {db.lookup(fn)}")
