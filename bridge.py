# -*- coding: utf-8 -*-
"""HTTP bridge that lets cdcheckd drive Picard from another container.

Endpoints (all JSON, all need ``Authorization: Bearer <token>``):

    GET  /status  Picard and plugin versions, and the "Clear existing tags" option.
    GET  /disc    Read the CD in the drive: disc ID, TOC string, MCN and ISRCs.
    POST /load    {paths[], release?, discid?}: load a release and add the files.

Handlers run on Qt's main thread, as Picard's own browser integration does.
The HTTP server itself runs on a worker thread.
"""

import hmac
import json
import os
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

MAX_BODY = 1 << 20
MAIN_THREAD_TIMEOUT = 60  # reading a disc is slow


class BridgeError(Exception):
    """An error reported to the client with an HTTP status."""
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Handlers:
    """The bridge's operations. Every method runs on the main thread."""

    def __init__(self, api: Any, plugin_version: str) -> None:
        self.api = api
        self.plugin_version = plugin_version

    def status(self, _: dict) -> dict:
        from picard import PICARD_VERSION_STR
        setting = self.api.global_config.setting
        return {
            'picard_version': PICARD_VERSION_STR,
            'plugin_version': self.plugin_version,
            'clear_existing_tags': bool(setting['clear_existing_tags']),
        }

    def disc(self, _: dict) -> dict:
        from picard.disc import Disc
        device = self.api.global_config.setting['cd_lookup_device'].split(',', 1)[0]
        disc = Disc()
        try:
            disc.read(device)
        except Exception as e:  # discid.DiscError, or no drive.
            raise BridgeError(409, f'could not read disc in {device}: {e}')
        # Picard 3: toc_string is set, tracks is a count, isrcs maps track -> ISRC.
        isrcs = [isrc for _, isrc in sorted((getattr(disc, 'isrcs', None) or {}).items())]
        return {
            'discid': disc.id,
            'toc': disc.toc_string or '',
            'mcn': getattr(disc, 'mcn', None) or '',
            'isrcs': isrcs,
        }

    def load(self, req: dict) -> dict:
        paths = req.get('paths')
        if (not isinstance(paths, list) or not paths
                or not all(isinstance(p, str) and os.path.isabs(p) for p in paths)):
            raise BridgeError(400, 'paths must be a non-empty list of absolute paths')
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            raise BridgeError(404, f'no such path: {missing[0]}')
        release = req.get('release') or ''
        discid = req.get('discid') or ''
        if not isinstance(release, str) or not isinstance(discid, str):
            raise BridgeError(400, 'release and discid must be strings')
        tagger = self.api.tagger
        if release:
            tagger.load_album(release, discid=discid or None)
        tagger.add_paths(paths)
        return {}


class Bridge:
    """Owns the HTTP server and its worker thread."""

    def __init__(self, handlers: Handlers, token: str, port: int,
                 call_on_main: Callable[[Callable[[], Any]], Any],
                 host: str = '0.0.0.0') -> None:
        self.routes = {
            ('GET', '/status'): handlers.status,
            ('GET', '/disc'): handlers.disc,
            ('POST', '/load'): handlers.load,
        }
        self.token = token
        self.call_on_main = call_on_main
        self._server = ThreadingHTTPServer((host, port), self._make_handler())
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.serve_forever, name='cdripper-bridge', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _make_handler(self) -> type:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                pass

            def _reply(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorized(self) -> bool:
                got = self.headers.get('Authorization', '')
                return hmac.compare_digest(got.encode(), f'Bearer {bridge.token}'.encode())

            def _handle(self, method: str) -> None:
                if not bridge.token or not self._authorized():
                    return self._reply(401, {'error': 'unauthorized'})
                fn = bridge.routes.get((method, self.path.split('?', 1)[0]))
                if fn is None:
                    return self._reply(404, {'error': 'not found'})
                req: dict = {}
                if method == 'POST':
                    try:
                        n = int(self.headers.get('Content-Length', '0'))
                        if n > MAX_BODY:
                            return self._reply(413, {'error': 'body too large'})
                        req = json.loads(self.rfile.read(n) or b'{}')
                        if not isinstance(req, dict):
                            raise ValueError('not an object')
                    except ValueError as e:
                        return self._reply(400, {'error': f'bad request: {e}'})
                try:
                    out = bridge.call_on_main(lambda: fn(req))
                except BridgeError as e:
                    return self._reply(e.status, {'error': str(e)})
                except FutureTimeout:
                    return self._reply(504, {'error': 'timed out waiting for Picard'})
                except Exception as e:
                    return self._reply(500, {'error': str(e)})
                self._reply(200, out)

            def do_GET(self) -> None:
                self._handle('GET')

            def do_POST(self) -> None:
                self._handle('POST')

        return Handler


def make_main_thread_caller() -> Callable[[Callable[[], Any]], Any]:
    """Return a function that runs a callable on Qt's main thread and waits.

    Must be created on the main thread: the helper object it builds lives there,
    so signals emitted from the HTTP worker threads are queued to it.
    """
    from PyQt6 import QtCore

    class Runner(QtCore.QObject):
        run = QtCore.pyqtSignal(object)

        def __init__(self) -> None:
            super().__init__()
            self.run.connect(self._run, QtCore.Qt.ConnectionType.QueuedConnection)

        @QtCore.pyqtSlot(object)
        def _run(self, job: tuple) -> None:
            fn, fut = job
            try:
                fut.set_result(fn())
            except BaseException as e:
                fut.set_exception(e)

    runner = Runner()

    def call(fn: Callable[[], Any]) -> Any:
        fut: Future = Future()
        runner.run.emit((fn, fut))
        return fut.result(timeout=MAIN_THREAD_TIMEOUT)

    return call
