# -*- coding: utf-8 -*-
"""HTTP bridge that lets cdcheckd drive Picard from another container.

Endpoints (all JSON, all need ``Authorization: Bearer <token>``):

    GET  /status  Picard and plugin versions, and the "Clear existing tags" option.
    GET  /disc    Read the CD in the drive: disc ID, TOC string, MCN and ISRCs.
    POST /load    {paths[], release?, discid?}: load a release and add the files
                  to it, with the disc ID so that saving writes MUSICBRAINZ_DISCID.
    POST /rerip   {files[], tracks[], discid?}: open the Verify and re-rip window
                  for the files (in track order) with the tracks ticked. The CD
                  in the drive must be discid.

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
SETTLE_POLL_MS = 500      # how often /load checks whether the files are placed
SETTLE_TIMEOUT_MS = 120_000


class BridgeError(Exception):
    """An error reported to the client with an HTTP status."""
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Handlers:
    """The bridge's operations. Every method runs on the main thread."""

    def __init__(self, api: Any, plugin_version: str,
                 schedule: Optional[Callable[[int, Callable[[], None]], None]] = None,
                 open_rerip: Optional[Callable[[list, list, dict], None]] = None) -> None:
        self.api = api
        self.plugin_version = plugin_version
        # schedule(ms, fn) runs fn on the main thread later; QTimer by default.
        self.schedule = schedule or _qt_single_shot
        # open_rerip(files, tracks, disc) opens the Verify and re-rip window.
        self.open_rerip = open_rerip

    def status(self, _: dict) -> dict:
        from picard import PICARD_VERSION_STR
        setting = self.api.global_config.setting
        return {
            'picard_version': PICARD_VERSION_STR,
            'plugin_version': self.plugin_version,
            'clear_existing_tags': bool(setting['clear_existing_tags']),
        }

    def disc(self, _: dict) -> dict:
        return read_disc(self.api)

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
        album = tagger.load_album(release, discid=discid or None) if release else None
        tagger.add_paths(paths)
        if album is not None:
            self._settle(album, paths, SETTLE_TIMEOUT_MS)
        return {}

    def rerip(self, req: dict) -> dict:
        files, tracks, discid = req.get('files'), req.get('tracks'), req.get('discid') or ''
        if (not isinstance(files, list) or not files
                or not all(isinstance(p, str) and os.path.isabs(p) for p in files)):
            raise BridgeError(400, 'files must be a non-empty list of absolute paths')
        if (not isinstance(tracks, list)
                or not all(type(n) is int and 1 <= n <= len(files) for n in tracks)):
            raise BridgeError(400, f'tracks must be track numbers from 1 to {len(files)}')
        if not isinstance(discid, str):
            raise BridgeError(400, 'discid must be a string')
        missing = [p for p in files if not os.path.isfile(p)]
        if missing:
            raise BridgeError(404, f'no such file: {missing[0]}')
        if self.open_rerip is None:
            raise BridgeError(501, 're-ripping is not available')
        disc = read_disc(self.api)
        if discid and disc['discid'] != discid:
            raise BridgeError(409, f'the CD in the drive is {disc["discid"]}, not {discid}')
        if len(disc['toc'].split()) - 3 != len(files):  # "1 N leadout offset1 ... offsetN"
            raise BridgeError(409, f'the CD in the drive does not have {len(files)} tracks')
        self.open_rerip(files, sorted(set(tracks)), disc)
        return {}

    def _settle(self, album: Any, paths: list, remaining_ms: int) -> None:
        """Move the loaded files onto ``album`` once Picard has placed them.

        Picard files each loaded file under the release its
        MUSICBRAINZ_ALBUMID names, and loads that release without a disc ID.
        When the request names a different release (another edition), the
        files would stay on the old one, and saving would not write
        MUSICBRAINZ_DISCID. Releases the move leaves empty are removed.
        """
        tagger = self.api.tagger
        files = [f for name, f in list(tagger.files.items()) if _under(name, paths)]
        if not files or any(_pending(f) for f in files):
            if remaining_ms > 0:
                self.schedule(SETTLE_POLL_MS,
                              lambda: self._settle(album, paths, remaining_ms - SETTLE_POLL_MS))
            else:
                self._log('warning', f'Bridge: files in {paths} did not load in time; '
                          f'move them to release {album.id} by hand')
            return
        on_album = set(map(id, album.iterfiles()))
        moved = [f for f in files if id(f) not in on_album]
        if not moved:
            return
        ours = set(map(id, moved))
        left = [a for a in tagger.albums.values()
                if a is not album and any(id(f) in ours for f in a.iterfiles())]
        tagger.move_files_to_album(moved, album=album)
        for other in left:
            if getattr(other, 'id', None) != 'NATS' and not any(True for _ in other.iterfiles()):
                tagger.remove_album(other)
        self._log('info', f'Bridge: moved {len(moved)} file(s) to release {album.id}')

    def _log(self, level: str, msg: str) -> None:
        logger = getattr(self.api, 'logger', None)
        if logger is not None:
            getattr(logger, level)(msg)


def read_disc(api: Any) -> dict:
    """Read the CD in Picard's CD lookup drive."""
    from picard.disc import Disc
    device = api.global_config.setting['cd_lookup_device'].split(',', 1)[0]
    disc = Disc()
    try:
        disc.read(device)
    except Exception as e:  # discid.DiscError, or no drive.
        raise BridgeError(409, f'could not read disc in {device}: {e}')
    return disc_info(disc)


def disc_info(disc: Any) -> dict:
    """The disc ID, TOC string, MCN and ISRCs of a disc Picard has read."""
    raw = getattr(disc, '_discid', None)
    toc = getattr(disc, 'toc_string', None) or getattr(raw, 'toc_string', '')
    # Picard 3 keeps ISRCs as {track number: ISRC}, and tracks is a count.
    found = getattr(disc, 'isrcs', None)
    if isinstance(found, dict):
        isrcs = [isrc for _, isrc in sorted(found.items()) if isrc]
    else:
        tracks = getattr(disc, 'tracks', None)
        isrcs = [t.isrc for t in tracks if getattr(t, 'isrc', None)] if isinstance(tracks, list) else []
    return {
        'discid': disc.id,
        'toc': toc or '',
        'mcn': getattr(disc, 'mcn', None) or '',
        'isrcs': isrcs,
    }


def _under(filename: str, paths: list) -> bool:
    """Whether filename is one of paths, or directly in one of them."""
    return any(filename == p or os.path.dirname(filename) == p.rstrip(os.sep) for p in paths)


def _pending(file: Any) -> bool:
    """Whether Picard is still loading the file or has not yet placed it."""
    state = getattr(file, 'state', None)
    return getattr(state, 'name', state) == 'PENDING' or getattr(file, 'parent_item', None) is None


def _qt_single_shot(ms: int, fn: Callable[[], None]) -> None:
    from PyQt6 import QtCore
    QtCore.QTimer.singleShot(ms, fn)


class Bridge:
    """Owns the HTTP server and its worker thread."""

    def __init__(self, handlers: Handlers, token: str, port: int,
                 call_on_main: Callable[[Callable[[], Any]], Any],
                 host: str = '0.0.0.0') -> None:
        self.routes = {
            ('GET', '/status'): handlers.status,
            ('GET', '/disc'): handlers.disc,
            ('POST', '/load'): handlers.load,
            ('POST', '/rerip'): handlers.rerip,
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
