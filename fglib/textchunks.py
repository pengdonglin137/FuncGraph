"""Collect exact Text network ranges from already encoded report fragments."""

from .output import SectionRange


TEXT_NETWORK_ROWS = 4096


class TextChunkRanges:
    def __init__(self, paint_rows):
        if type(paint_rows) is not int or paint_rows <= 0 or TEXT_NETWORK_ROWS % paint_rows:
            raise ValueError('Text paint rows must divide the network chunk size')
        self.marker = f'<div class="text-chunk" data-chunk-size="{paint_rows}">'.encode('ascii')
        self.paint_rows = paint_rows
        self.rows = 0
        self.ranges = []
        self.start = None
        self.end = None
        self.open_rows = None
        self.partial = False
        self.ended = False

    def append(self, name, payload, start, end):
        if name != 'text':
            if self.end is not None:
                self.ended = True
            return
        if self.ended or self.end is not None and self.end != start:
            raise ValueError('Text fragments must be contiguous')
        if payload == self.marker:
            if self.open_rows is not None or self.partial:
                raise ValueError('Invalid Text paint chunk opening')
            self.open_rows = 0
            if self.start is None:
                self.start = start
        elif payload == b'</div>':
            if self.open_rows is None or not 0 < self.open_rows <= self.paint_rows:
                raise ValueError('Invalid Text paint chunk closing')
            self.partial = self.open_rows < self.paint_rows
            self.open_rows = None
            if self.rows % TEXT_NETWORK_ROWS == 0:
                self.ranges.append((self.rows - TEXT_NETWORK_ROWS, TEXT_NETWORK_ROWS,
                                    SectionRange('text', self.start, end)))
                self.start = None
        else:
            if self.open_rows is None or self.open_rows >= self.paint_rows:
                raise ValueError('Invalid Text row fragment')
            self.open_rows += 1
            self.rows += 1
        self.end = end

    def finish(self, expected_rows):
        if self.open_rows is not None or self.rows != expected_rows:
            raise ValueError('Text chunks do not match the complete filter index')
        if self.start is not None:
            count = self.rows % TEXT_NETWORK_ROWS
            self.ranges.append((self.rows - count, count, SectionRange('text', self.start, self.end)))
            self.start = None
        return self.ranges
