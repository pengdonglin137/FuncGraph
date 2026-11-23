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
import re
import struct
import zlib
from functools import lru_cache

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
COMPILER_SUFFIX = re.compile(
    r'(?:\.(?:isra|constprop|part|clone|lto|lto_priv)\.\d+|'
    r'\.(?:cold|hot)(?:\.\d+)?|\.llvm\.[0-9a-fA-F]+)+$')


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

    def __init__(self, blob, base=None, source='', word_size=None):
        self.source = source
        self.elf_source = None
        self.base = base
        self.word_size = word_size or (base.word_size if base else None)
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
        self.enum_offsets = {}
        self._parse_types()
        self.next_id = self.start_id + len(self.kinds)
        self.func_ret = {}
        self.func_params = {}
        self.func_proto = {}
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
            if pos + fixed + per * vlen > n:
                raise BtfError(f'{self.source}: truncated type record')
            extra = 0
            if kind == KIND_INT:
                extra, = unpack(e + 'I', raw, pos)
            elif kind in (KIND_ENUM, KIND_ENUM64):
                extra = kflag
                self.enum_offsets[len(self.kinds)] = pos
            elif kind == KIND_FWD:
                extra = kflag
            elif kind == KIND_ARRAY:
                extra = unpack(e + 'III', raw, pos)
            elif kind == KIND_FUNC_PROTO:
                extra = tuple(unpack(e + 'II', raw, pos + index * 8) for index in range(vlen))
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
            return ('senum' if extra else 'enum', st)
        return ('other', st if kind in (KIND_STRUCT, KIND_UNION) else 0)

    def _collect_functions(self):
        """Reuse shared layouts during this object's indexing pass."""
        @lru_cache(maxsize=4096)
        def parameter(offset, type_id):
            return (self.string(offset), *self.classify(type_id), self.scalar_bits(type_id))

        @lru_cache(maxsize=4096)
        def return_layout(type_id):
            kind_size = self.classify(type_id)
            return kind_size, (*kind_size, self.scalar_bits(type_id, returning=True))

        @lru_cache(maxsize=4096)
        def prototype_layout(type_id):
            kind, returned, fields = self._get(type_id)
            if kind != KIND_FUNC_PROTO:
                return None
            kind_size, returned_layout = return_layout(returned)
            return kind_size, (tuple(parameter(offset, field) for offset, field in fields),
                               returned_layout, self.word_size)

        for i, kind in enumerate(self.kinds):
            if kind != KIND_FUNC:
                continue
            proto = self.sizetype[i]
            layout = prototype_layout(proto)
            if layout is None:
                continue
            name = self.string(self.names[i])
            if self.func_proto.setdefault(name, proto) != proto:
                self.func_proto[name] = None
            returned, parameters = layout
            self.func_ret[name] = returned
            if name in self.func_params and self.func_params[name] != parameters:
                self.func_params[name] = None
            else:
                self.func_params[name] = parameters

    def _owner(self, type_id):
        owner = self
        while owner is not None and type_id < owner.start_id:
            owner = owner.base
        return owner if owner is not None and type_id < owner.next_id else None

    def enum_type(self, type_id):
        for _ in range(33):
            kind, target, _ = self._get(type_id)
            if kind in (KIND_ENUM, KIND_ENUM64):
                return self._owner(type_id), type_id
            if kind not in _MODIFIERS:
                break
            type_id = target
        return None

    def enum_values(self, type_id):
        kind, size, signed = self._get(type_id)
        index = type_id - self.start_id
        offset = self.enum_offsets[index]
        bits = size * 8
        payload_bits = 32 if kind == KIND_ENUM else 64
        names = {}
        for entry in range(self.vlens[index]):
            fields = struct.unpack_from(self.end + ('II' if kind == KIND_ENUM else 'III'), self._types_raw,
                                        offset + entry * (8 if kind == KIND_ENUM else 12))
            value = fields[1] | (fields[2] << 32 if kind == KIND_ENUM64 else 0)
            if signed and value & (1 << (payload_bits - 1)):
                value -= 1 << payload_bits
            value &= (1 << bits) - 1
            if signed and value & (1 << (bits - 1)):
                value -= 1 << bits
            name = self.string(fields[0])
            if name:
                names.setdefault(str(value), []).append(name)
        return {'name': self.string(self.names[index]), 'bits': bits, 'signed': bool(signed),
                'enum64': kind == KIND_ENUM64, 'values': names}

    def _parameters_text(self, proto, depth):
        owner = self._owner(proto)
        if owner is None:
            return None
        _, _, fields = self._get(proto)
        parameters = []
        for position, (offset, type_id) in enumerate(fields):
            if not type_id:
                if offset or not position or position != len(fields) - 1:
                    return None
                parameters.append('...')
                continue
            declaration = self._declare(type_id, owner.string(offset), depth + 1)
            if declaration is None:
                return None
            parameters.append(declaration)
        return parameters or ['void']

    def _declare(self, type_id, declarator, depth=0, qualifiers=()):
        if depth > 32:
            return None
        if type_id == 0:
            return ' '.join((*qualifiers, 'void', declarator)).rstrip()
        owner = self._owner(type_id)
        if owner is None:
            return None
        kind, target, extra = self._get(type_id)
        if kind in (KIND_CONST, KIND_VOLATILE, KIND_RESTRICT, KIND_TYPE_TAG):
            qualifier = {KIND_CONST: 'const', KIND_VOLATILE: 'volatile', KIND_RESTRICT: 'restrict'}.get(kind)
            return self._declare(target, declarator, depth + 1, (*qualifiers, qualifier) if qualifier else qualifiers)
        if kind == KIND_PTR:
            pointer = '* ' + ' '.join((*qualifiers, declarator)).rstrip() if qualifiers else '*' + declarator
            return self._declare(target, pointer.rstrip(), depth + 1)
        if kind in (KIND_ARRAY, KIND_FUNC_PROTO):
            nested = f'({declarator})' if declarator.startswith('*') else declarator
            if kind == KIND_ARRAY:
                element, _, length = extra
                return self._declare(element, f'{nested}[{length}]', depth + 1, qualifiers)
            parameters = self._parameters_text(type_id, depth)
            return self._declare(target, f'{nested}({", ".join(parameters)})', depth + 1) if parameters else None
        name = owner.string(owner.names[type_id - owner.start_id])
        if not name:
            return None
        tag = {KIND_STRUCT: 'struct', KIND_UNION: 'union', KIND_ENUM: 'enum', KIND_ENUM64: 'enum',
               KIND_FWD: 'union' if extra else 'struct'}.get(kind)
        if tag:
            name = f'{tag} {name}'
        elif kind not in (KIND_INT, KIND_TYPEDEF, KIND_FLOAT):
            return None
        return ' '.join((*qualifiers, name, declarator)).rstrip()

    def _prototype_parts(self, proto):
        parameters = self._parameters_text(proto, 0)
        if not parameters:
            return None
        _, returned, _ = self._get(proto)
        form = ',\n    '.join(parameters)
        return returned, f'(\n    {form}\n)' if len(parameters) > 1 else f'({form})'

    def prototype(self, proto, name, *, parts_reader=None):
        parts = self._prototype_parts(proto) if parts_reader is None else parts_reader(self, proto)
        if parts is None:
            return None
        returned, parameters = parts
        declaration = self._declare(returned, name + parameters)
        return declaration + ';' if declaration else None

    def scalar_bits(self, tid, returning=False, _depth=0):
        if tid == 0:
            return 0
        if _depth > 32:
            return None
        kind, size, extra = self._get(tid)
        if kind in _MODIFIERS:
            return self.scalar_bits(size, returning, _depth + 1)
        if kind == KIND_PTR:
            return self.word_size * 8 if self.word_size else None
        if kind == KIND_INT:
            bits, offset = extra & 0xff, (extra >> 16) & 0xff
            if offset or not 0 < bits <= min(size * 8, 64):
                return None
            return 1 if returning and (extra >> 24) & 4 else bits
        if kind in (KIND_ENUM, KIND_ENUM64) and 0 < size <= 8:
            return size * 8
        return None

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
    info = BtfInfo(blob, base=base, source=path,
                   word_size=(8 if data[4] == 2 else 4) if data[:4] == b'\x7fELF' else None)
    if data[:4] == b'\x7fELF':
        info.elf_source = path
    return info


def format_scalar(value, kind, bits, returning=False):
    if kind == 'void':
        return None
    if not isinstance(bits, int) or not 0 < bits <= 64:
        return value
    try:
        number = int(value, 16 if value.lower().startswith('0x') else 10) & ((1 << bits) - 1)
    except (ValueError, AttributeError):
        return value
    if kind in ('sint', 'senum'):
        if number & (1 << (bits - 1)):
            number -= 1 << bits
        return str(number)
    if kind == 'enum':
        return str(number)
    if kind == 'bool' and returning:
        return 'true' if number else 'false'
    return hex(number)


class ReturnTypeDB:
    """Index return types and trusted argument prototypes from kernel/module BTF."""

    def __init__(self):
        self.funcs = {}
        self.params = {}
        self.sources = []
        self.errors = []
        self._argument_sources = []
        self._argument_prototypes = {}
        self._prototype_owners = {}
        self._prototype_modules = {}

    def add(self, info):
        if info is None:
            return
        # Module (split) BTF only contributes its own functions
        for k, v in info.func_ret.items():
            self.funcs.setdefault(k, v)
        for name, params in info.func_params.items():
            if name in self.params and self.params[name] != params:
                self.params[name] = None
            else:
                self.params[name] = params
            owner = self._prototype_owners.get(name)
            if owner is None:
                self._prototype_owners[name] = info
            elif isinstance(owner, list):
                owner.append(info)
            else:
                self._prototype_owners[name] = [owner, info]
        self._prototype_modules[info] = os.path.basename(info.source).split('.ko', 1)[0].replace('-', '_') if info.source else None
        self.sources.append(info.source)
        if info.elf_source:
            self._argument_sources.append((info.elf_source, info.func_params))
        self._argument_prototypes.clear()

    def __len__(self):
        return len(self.funcs)

    def prepare_arguments(self, func_names):
        """Batch and cache ABI checks for suffix-stripped, non-exact BTF matches."""
        from .btf_abi import verified_clones
        requests = {}
        candidates = {}
        for name in dict.fromkeys(func_names):
            if name in self.params or name in self._argument_prototypes:
                continue
            self._argument_prototypes[name] = None
            base = COMPILER_SUFFIX.sub('', name)
            params = self.params.get(base) if base != name else None
            if params is None:
                continue
            fields, returned, word_size = params
            if returned[0] == 'other' or word_size is None or any(
                    not field or bits is None or not 0 < bits <= 64 or
                    kind not in ('ptr', 'sint', 'uint', 'bool', 'enum', 'senum') or not 0 < size <= word_size
                    for field, kind, size, bits in fields):
                continue
            paths = {path for path, prototypes in self._argument_sources if prototypes.get(base) == params}
            if not paths:
                continue
            candidates[name] = (params, paths)
            for path in paths:
                requests.setdefault(path, {})[name] = (base, fields, word_size)
        verified = {path: verified_clones(path, names) for path, names in requests.items()}
        for name, (params, paths) in candidates.items():
            if all(name in verified[path] for path in paths):
                self._argument_prototypes[name] = params

    def _parameters(self, func_name):
        if func_name in self.params:
            return self.params[func_name], True
        self.prepare_arguments((func_name,))
        return self._argument_prototypes.get(func_name), False

    def argument_names(self, func_name, count):
        """Name only an ABI-safe prefix of captured integer argument slots."""
        params, _exact = self._parameters(func_name)
        if params is None:
            return ()
        fields, returned, word_size = params
        if returned[0] == 'other':
            return ()
        names = []
        for name, kind, size, bits in fields[:count]:
            if kind != 'ptr' and (kind not in ('sint', 'uint', 'bool', 'enum', 'senum') or
                                  not 0 < size <= (word_size or 4)):
                break
            names.append(name)
        return tuple(names)

    def function_types(self, func_name):
        params, exact = self._parameters(func_name)
        if params is None:
            return None
        fields, returned, word_size = params
        result = {}
        if returned[0] != 'other':
            arguments = []
            for name, kind, size, bits in fields:
                if bits is None or not bits or size > (word_size or 4) and kind != 'ptr':
                    break
                arguments.append((kind, bits))
            result['args'] = arguments
            if len(arguments) == len(fields):
                result['argc'] = len(fields)
        kind, size, bits = returned
        if exact and bits is not None and (kind == 'void' or kind == 'ptr' or size <= (word_size or 4)):
            result['ret'] = (kind, bits)
        return result or None

    def display_prototype(self, name, module):
        owner = self._prototype_owners.get(name)
        exact = owner is not None
        symbol = name
        if not exact:
            if not self._parameters(name)[0]:
                return None
            symbol = COMPILER_SUFFIX.sub('', name)
            owner = self._prototype_owners.get(symbol)
        if module:
            module = module.replace('-', '_')
            if isinstance(owner, list):
                candidates = [info for info in owner if self._prototype_modules[info] == module]
                owner = candidates[0] if len(candidates) == 1 else None
            elif self._prototype_modules.get(owner) != module:
                return None
        if owner is None or isinstance(owner, list):
            return None
        proto = owner.func_proto[symbol]
        return (owner, proto, symbol, exact) if proto is not None else None

    def detail_data(self, symbols):
        """Project prototypes and enum tables for fields with recorded values."""
        symbols = tuple(symbols)
        self.prepare_arguments(name for _, name, _, _, _ in symbols if name not in self._prototype_owners)
        functions, enums, enum_ids = {}, [], {}
        prototype_parts = lru_cache(maxsize=256)(BtfInfo._prototype_parts)
        source_name = lru_cache(maxsize=128)(os.path.basename)

        @lru_cache(maxsize=4096)
        def enum_id(info, type_id):
            identity = info.enum_type(type_id)
            if identity is None:
                return None
            owner, enum_type_id = identity
            if not owner.vlens[enum_type_id - owner.start_id]:
                return None
            if identity not in enum_ids:
                enum_ids[identity] = len(enums)
                enums.append(owner.enum_values(enum_type_id))
            return enum_ids[identity]

        for key, name, module, has_args, has_ret in symbols:
            match = self.display_prototype(name, module)
            if match is None:
                continue
            info, proto, symbol, exact = match
            entry = {}
            declaration = info.prototype(proto, symbol, parts_reader=prototype_parts)
            if declaration:
                entry['prototype'] = declaration
                if not exact:
                    entry['clone'] = True
                if info.source:
                    entry['source'] = source_name(info.source)
            owner = info._owner(proto)
            _, returned, parameters = info._get(proto)
            return_kind, return_size = info.classify(returned)
            word_size = info.word_size or 4
            if has_ret and exact and return_kind in ('enum', 'senum') and 0 < return_size <= word_size:
                ret = enum_id(info, returned)
                if ret is not None:
                    entry['ret'] = ret
            args = {}
            if has_args and return_kind != 'other':
                for position, (offset, type_id) in enumerate(parameters):
                    kind, size = info.classify(type_id)
                    if kind != 'ptr' and (kind not in ('sint', 'uint', 'bool', 'enum', 'senum') or
                                          not 0 < size <= word_size or not info.scalar_bits(type_id)):
                        break
                    if kind in ('enum', 'senum'):
                        arg = enum_id(info, type_id)
                        if arg is not None:
                            args[position] = [owner.string(offset), arg]
            if args:
                entry['args'] = args
            if entry:
                functions[key] = entry
        return {'functions': functions, 'enums': enums} if functions else None

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


class ReturnTypeLoader:
    """Lazily reuse BTF sources while keeping requested module databases separate."""

    def __init__(self, vmlinux=None, extra_paths=None):
        self.vmlinux = vmlinux
        self.extra_paths = tuple(extra_paths or ())
        self._sources = {}
        self._databases = {}

    def _load(self, path, base, database):
        key = (path, base)
        if key not in self._sources:
            try:
                self._sources[key] = (load_btf(path, base=base), None)
            except (OSError, BtfError, struct.error, EOFError, lzma.LZMAError, zlib.error) as error:
                self._sources[key] = (None, f"{path}: {error}")
        info, error = self._sources[key]
        if error is not None:
            database.errors.append(error)
        return info

    def build(self, module_paths=None):
        modules = tuple(dict.fromkeys(module_paths or ()))
        if modules in self._databases:
            return self._databases[modules]
        database = ReturnTypeDB()
        base = None
        candidates = (self.vmlinux,) if self.vmlinux else ()
        for path in candidates + self.extra_paths:
            info = self._load(path, None, database)
            if info is not None and info.kinds:
                base = info
                database.add(info)
                break
        for path in self.extra_paths + modules:
            if base is not None and path == base.source:
                continue
            if path in self.extra_paths and os.path.basename(path).startswith('vmlinux'):
                continue
            database.add(self._load(path, base, database))
        self._databases[modules] = database
        return database


def build_return_db(vmlinux=None, extra_paths=None, module_paths=None, verbose=False):
    """Build a return-type database; reuse ReturnTypeLoader for one report's views.

    extra_paths accepts raw BTF or ELF files. A raw blob named 'vmlinux' is a
    base candidate when vmlinux has no .BTF. Module files use split BTF.
    """
    return ReturnTypeLoader(vmlinux, extra_paths).build(module_paths)


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
