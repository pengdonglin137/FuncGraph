"""Counted report sections and atomic publication without a complete HTML string."""

from dataclasses import dataclass
import os
import stat
from typing import Tuple
import uuid


@dataclass(frozen=True)
class SectionRange:
    name: str
    start: int
    end: int


@dataclass(frozen=True)
class WrittenReport:
    path: str
    byte_count: int
    sections: Tuple[SectionRange, ...]


class ReportSpool:
    """Stage UTF-8 sections beside a destination; publish only a complete render."""

    def __init__(self, path):
        self.path = os.fspath(path)
        self.destination = os.path.realpath(self.path)
        self.temporary = None
        self.file = None
        self.byte_count = 0
        self.started = False
        self.finished = False
        self.published = False
        self._ranges = []

    def __enter__(self):
        if self.file is not None:
            raise RuntimeError('report spool cannot be reopened')
        mode = None
        try:
            previous = os.stat(self.destination)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(previous.st_mode):
                raise OSError('report destination is not a regular file: ' + self.path)
            mode = stat.S_IMODE(previous.st_mode)
        parent = os.path.dirname(self.destination)
        temporary = os.path.join(parent, '.funcgraph-' + uuid.uuid4().hex + '.tmp')
        self.file = open(temporary, 'x+b')
        self.temporary = temporary
        try:
            if mode is not None:
                os.fchmod(self.file.fileno(), mode)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, error_type, error, traceback):
        try:
            if self.file is not None:
                self.file.close()
        finally:
            if self.temporary is not None and not self.published:
                try:
                    os.unlink(self.temporary)
                except FileNotFoundError:
                    pass

    @property
    def sections(self):
        return tuple(SectionRange(*section) for section in self._ranges)

    def write_sections(self, sections):
        if self.file is None or self.file.closed or self.started:
            raise RuntimeError('report sections can only be written once to an open spool')
        self.started = True
        for name, fragment in sections:
            if not isinstance(name, str) or not name or not isinstance(fragment, str):
                raise TypeError('report sections require a name and a text fragment')
            encoded = fragment.encode('utf-8')
            if not encoded:
                continue
            start = self.byte_count
            if self.file.write(encoded) != len(encoded):
                raise OSError('short write while staging the report')
            self.byte_count += len(encoded)
            if self._ranges and self._ranges[-1][0] == name:
                self._ranges[-1][2] = self.byte_count
            else:
                self._ranges.append([name, start, self.byte_count])
        self.finished = True

    def exceeds_threshold(self, byte_limit):
        if not self.finished:
            raise RuntimeError('report size is not final until rendering completes')
        if isinstance(byte_limit, bool) or not isinstance(byte_limit, int) or byte_limit < 0:
            raise ValueError('report size threshold must be a non-negative integer byte count')
        return self.byte_count > byte_limit

    def copy_sections(self, name, destination):
        """Copy matching ranges in order, preserving the spool position and bounded reads."""
        if not self.finished or self.file is None or self.file.closed:
            raise RuntimeError('report sections require a complete open spool')
        position = self.file.tell()
        self.file.flush()
        try:
            for section in self.sections:
                if section.name != name:
                    continue
                self.file.seek(section.start)
                remaining = section.end - section.start
                while remaining:
                    block = self.file.read(min(remaining, 1024 * 1024))
                    if not block:
                        raise OSError('report spool ended before its recorded section boundary')
                    if destination.write(block) != len(block):
                        raise OSError('short write while copying report sections')
                    remaining -= len(block)
        finally:
            self.file.seek(position)

    def publish(self):
        if not self.finished or self.published or self.file is None or self.file.closed:
            raise RuntimeError('only a complete open report can be published once')
        self.file.flush()
        os.fsync(self.file.fileno())
        os.replace(self.temporary, self.destination)
        self.published = True
        return WrittenReport(self.path, self.byte_count, self.sections)
