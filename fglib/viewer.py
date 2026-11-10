"""Read-only loopback HTTP access to an explicitly selected report."""

import argparse
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
import os
from pathlib import Path
import secrets
import stat
import sys
import threading
from urllib.parse import quote, unquote, urlsplit
import webbrowser

from .constants import APP_VERSION


def _open_directory(path):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            nested = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = nested
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class ReportFiles:
    def __init__(self, path):
        if os.open not in os.supports_dir_fd or not all(hasattr(os, name) for name in ['O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK']):
            raise OSError('the report viewer requires directory-relative, no-follow file access')
        try:
            selected = Path(path).resolve(strict=True)
        except RuntimeError as error:
            raise OSError('cannot resolve the selected report path: ' + str(error)) from error
        metadata = selected.stat()
        if stat.S_ISDIR(metadata.st_mode):
            self.root = selected
            self.entry = 'index.html'
            self.single = False
        elif stat.S_ISREG(metadata.st_mode):
            self.root = selected.parent
            self.entry = selected.name
            self.single = True
        else:
            raise OSError('select an HTML report or a report directory with index.html')
        selected_identity = (metadata.st_dev, metadata.st_ino)
        self.lock = threading.Lock()
        self.descriptor = _open_directory(self.root)
        try:
            root_metadata = os.fstat(self.descriptor)
            if not self.single and (root_metadata.st_dev, root_metadata.st_ino) != selected_identity:
                raise OSError('the selected report directory changed while opening it; try again')
            stream, metadata = self.open(self.entry)
            stream.close()
            if self.single and (metadata.st_dev, metadata.st_ino) != selected_identity:
                raise OSError('the selected report changed while opening it; try again')
        except BaseException:
            self.close()
            raise

    def close(self):
        with self.lock:
            if self.descriptor is not None:
                os.close(self.descriptor)
                self.descriptor = None

    def open(self, relative):
        parts = relative.split('/')
        if (not parts or any(not part or part in ('.', '..') or '\\' in part or '\x00' in part for part in parts)
                or (relative != self.entry and any(part.startswith('.') for part in parts))
                or (self.single and relative != self.entry)):
            raise FileNotFoundError(errno.ENOENT, 'report resource not found')
        with self.lock:
            if self.descriptor is None:
                raise FileNotFoundError(errno.ENOENT, 'the report viewer is closed')
            directory = os.dup(self.descriptor)
        try:
            for part in parts[:-1]:
                nested = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = nested
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise FileNotFoundError(errno.ENOENT, 'report resource is not a regular file')
            stream = os.fdopen(descriptor, 'rb')
        except BaseException:
            os.close(descriptor)
            raise
        return stream, metadata


class ReportHandler(BaseHTTPRequestHandler):
    server_version = 'FuncGraphViewer'
    sys_version = ''
    timeout = 15

    def log_message(self, format, *arguments):
        pass

    def _headers(self, status, length, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Content-Security-Policy',
                         "default-src 'none'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                         "font-src 'self' data:; connect-src 'self'; "
                         "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()

    def _error(self, status, message, head):
        body = (message + '\n').encode('utf-8')
        self._headers(status, len(body), 'text/plain; charset=utf-8')
        if not head:
            self.wfile.write(body)

    def _serve(self, head=False):
        hosts = self.headers.get_all('Host', [])
        origins = self.headers.get_all('Origin', [])
        if (len(hosts) != 1 or hosts[0].lower() not in self.server.trusted_hosts
                or len(origins) > 1 or (origins and origins[0] != 'http://' + hosts[0].lower())):
            self._error(403, 'Local report requests only.', head)
            return
        try:
            request = urlsplit(self.path)
            path = unquote(request.path, encoding='utf-8', errors='strict')
        except (ValueError, UnicodeError):
            self._error(404, 'Report resource not found.', head)
            return
        prefix = '/' + self.server.token + '/'
        if request.scheme or request.netloc or request.fragment or not path.startswith(prefix):
            self._error(404, 'Report resource not found.', head)
            return
        relative = path[len(prefix):] or self.server.files.entry
        try:
            stream, metadata = self.server.files.open(relative)
        except (OSError, ValueError):
            self._error(404, 'Report resource not found.', head)
            return
        with stream:
            content_type = ('text/html; charset=utf-8' if relative == self.server.files.entry else
                            mimetypes.guess_type(relative)[0] or 'application/octet-stream')
            self._headers(200, metadata.st_size, content_type)
            if head:
                return
            remaining = metadata.st_size
            while remaining:
                block = stream.read(min(remaining, 1024 * 1024))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def do_GET(self):
        try:
            self._serve()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        try:
            self._serve(head=True)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _readonly(self):
        self.close_connection = True
        self.send_response(405)
        self.send_header('Allow', 'GET, HEAD')
        self.send_header('Content-Length', '0')
        self.end_headers()

    do_POST = _readonly
    do_PUT = _readonly
    do_PATCH = _readonly
    do_DELETE = _readonly
    do_OPTIONS = _readonly


class ReportServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, files, port):
        self.files = files
        self.token = secrets.token_urlsafe(24)
        super().__init__(('127.0.0.1', port), ReportHandler)
        actual_port = self.server_address[1]
        self.trusted_hosts = {'127.0.0.1:' + str(actual_port), 'localhost:' + str(actual_port)}
        if actual_port == 80:
            self.trusted_hosts.update(['127.0.0.1', 'localhost'])
        self.url = 'http://127.0.0.1:' + str(actual_port) + '/' + self.token + '/' + quote(files.entry)

    def server_close(self):
        try:
            super().server_close()
        finally:
            self.files.close()


def create_server(path, port=0):
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError('port must be an integer between 0 and 65535')
    files = ReportFiles(path)
    try:
        return ReportServer(files, port)
    except BaseException:
        files.close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(prog='fgview', description='Open one report through a read-only, loopback-only HTTP viewer.')
    parser.add_argument('report', help='single HTML file, or a dedicated report directory containing index.html')
    parser.add_argument('--port', type=int, default=0, help='local port (default: choose an available port)')
    parser.add_argument('--open', dest='open_browser', action='store_true', help='also open the URL in the default browser')
    parser.add_argument('--version', action='version', version='FuncGraph ' + APP_VERSION)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    try:
        server = create_server(args.report, args.port)
    except (OSError, ValueError) as error:
        parser.exit(2, 'fgview: error: ' + str(error) + '\n')
    try:
        print(server.url, flush=True)
        print('Read-only local viewer. Press Ctrl+C to stop.', flush=True)
        if args.open_browser:
            try:
                opened = webbrowser.open(server.url)
            except (OSError, webbrowser.Error) as error:
                print('Could not open a browser: ' + str(error), file=sys.stderr)
            else:
                if not opened:
                    print('Could not open a browser; open the printed URL manually.', file=sys.stderr)
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
