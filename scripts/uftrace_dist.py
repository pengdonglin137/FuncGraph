"""Validate and stage uftrace binaries together with their corresponding source."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parent.parent
MACHINES = {'x86_64': 62, 'aarch64': 183}
LIBRARIES = ('libmcount.so', 'libmcount-single.so', 'libmcount-fast.so',
             'libmcount-fast-single.so', 'libmcount-nop.so')


def host_architecture():
    architecture = platform.machine()
    if platform.system() != 'Linux' or architecture not in MACHINES:
        raise ValueError('bundled uftrace wheels require native Linux x86_64 or aarch64')
    return architecture


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(directory, *arguments):
    return subprocess.check_output(['git', '-C', str(directory), *arguments], stderr=subprocess.PIPE)


def provenance(prefix, root):
    patches = root / 'patches' / 'uftrace'
    indexed = git(root, 'ls-files', '--stage', 'third_party/uftrace').decode().split()
    if len(indexed) != 4 or indexed[0] != '160000' or indexed[2] != '0':
        raise ValueError('native wheels require a checkout with the pinned uftrace submodule')
    pin = indexed[1]
    if json.loads((root / 'third_party/versions.json').read_text()).get('uftrace') != pin:
        raise ValueError('uftrace gitlink differs from third_party/versions.json')
    upstream = root / 'third_party' / 'uftrace'
    if not (upstream / '.git').exists() or git(upstream, 'rev-parse', 'HEAD').decode().strip() != pin:
        raise ValueError('initialize the pinned uftrace submodule before building a native wheel')
    if git(upstream, 'status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('pinned uftrace sources have tracked edits')
    if (prefix / 'funcgraph-uftrace.pin').read_text().strip() != pin:
        raise ValueError('uftrace build does not match the indexed upstream pin')
    names = [line for line in (patches / 'series').read_text().splitlines() if line and not line.startswith('#')]
    if len(names) != len(set(names)) or not all(re.fullmatch(r'[\w-][\w.-]*\.patch', name) for name in names):
        raise ValueError('invalid uftrace patch series')
    checksums = ''.join(digest(patches / name) + '  ' + name + '\n' for name in names)
    if (prefix / 'funcgraph-patches.sha256').read_text() != checksums:
        raise ValueError('uftrace build patch checksums are stale; rebuild the native prefix')
    return pin, names, checksums


def validate_elf(path, architecture):
    if not path.is_file() or path.is_symlink():
        raise ValueError('missing regular uftrace payload: ' + str(path))
    with path.open('rb') as stream:
        header = stream.read(20)
    if len(header) != 20 or header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != MACHINES[architecture]:
        raise ValueError('uftrace payload ELF architecture mismatch: ' + str(path))


def source_archive(destination, root, pin, names, checksums):
    upstream, patches = root / 'third_party' / 'uftrace', root / 'patches' / 'uftrace'
    with tempfile.TemporaryDirectory(prefix='fg-uftrace-source-') as directory:
        tree = Path(directory) / 'uftrace'
        tree.mkdir()
        with tarfile.open(fileobj=io.BytesIO(git(upstream, 'archive', pin))) as archive:
            for member in archive.getmembers():
                if member.name.startswith('/') or '..' in Path(member.name).parts or member.issym() or member.islnk():
                    raise ValueError('unsupported source archive member: ' + member.name)
            archive.extractall(tree)
        for name in names:
            subprocess.run(['git', '--git-dir=' + str(upstream / '.git'), '--work-tree=' + str(tree),
                            '-C', str(tree), 'apply', str(patches / name)], check=True, capture_output=True)
        metadata = tree / 'funcgraph'
        metadata.mkdir()
        for name in ['series'] + names:
            shutil.copyfile(patches / name, metadata / name)
        (metadata / 'upstream.pin').write_text(pin + '\n')
        (metadata / 'patches.sha256').write_text(checksums)
        (metadata / 'BUILDING').write_text(
            'Corresponding source for the bundled FuncGraph uftrace (GPL-2.0).\n'
            'The patch series is already applied. Build on Linux x86_64 or aarch64.\n'
            'Ubuntu 22.04 prerequisites: build-essential pkg-config libtraceevent-dev libelf-dev libdw-dev.\n'
            './configure --prefix="$PWD/install" --without-libpython --without-libluajit '
            '--without-libncurses --without-libunwind --without-capstone\n'
            'make -j2 && make install\n'
            'Use --libmcount-path="$PWD/install/lib/uftrace" when relocating the installation.\n')
        with destination.open('wb') as output, gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for path in sorted(tree.rglob('*')):
                    item = archive.gettarinfo(str(path), 'uftrace/' + path.relative_to(tree).as_posix())
                    item.uid = item.gid = item.mtime = 0
                    item.uname = item.gname = ''
                    if path.is_file():
                        with path.open('rb') as stream:
                            archive.addfile(item, stream)
                    else:
                        archive.addfile(item)


def stage_payload(prefix, destination, root=ROOT):
    architecture = host_architecture()
    prefix, destination = prefix.resolve(), Path(destination)
    if destination.exists():
        raise ValueError('uftrace payload destination already exists: ' + str(destination))
    pin, names, checksums = provenance(prefix, root)
    files = ['bin/uftrace'] + ['lib/uftrace/' + name for name in LIBRARIES]
    for name in files:
        validate_elf(prefix / name, architecture)
    if not os.access(prefix / 'bin/uftrace', os.X_OK):
        raise ValueError('uftrace binary is not executable')
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir()
        for name in files:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(prefix / name, target)
            target.chmod(0o755)
        for name in ('funcgraph-uftrace.pin', 'funcgraph-patches.sha256'):
            shutil.copyfile(prefix / name, destination / name)
        shutil.copyfile(root / 'third_party' / 'uftrace' / 'COPYING', destination / 'COPYING')
        source_archive(destination / 'source.tar.gz', root, pin, names, checksums)
        manifest = {'architecture': architecture, 'upstream': pin, 'license': 'GPL-2.0',
                    'files': {path.relative_to(destination).as_posix(): digest(path)
                              for path in sorted(destination.rglob('*')) if path.is_file()}}
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prefix', type=Path)
    parser.add_argument('destination', type=Path)
    arguments = parser.parse_args()
    stage_payload(arguments.prefix, arguments.destination)


if __name__ == '__main__':
    main()
