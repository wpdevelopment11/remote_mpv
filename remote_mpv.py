from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import mimetypes
import os
import re
import shutil
from urllib import parse as urlparse

from python_mpv_jsonipc import MPV as Mpv
from python_mpv_jsonipc import MPVError

IPC_SOCKET = "mpvsocket"
LISTEN = ("127.0.0.1", 7271)
STATIC_ROOT = "static"

ALLOWED_PROPERTIES = ["pause", "playlist", "speed", "mute", "volume", "volume-max"]
ALLOWED_COMMANDS = ["add", "playlist-play-index", "playlist_next", "playlist_prev", "seek", "set", "show_text", "multiply"]

class Route:
    regex = None

    def __init__(self, prefix):
        self.prefix = prefix

    def match(self, path):
        return self.regex.fullmatch(path)

    def path_getter_re(self):
        return re.compile("/{}/([^/]+)/?".format(self.prefix))

    def path_setter_re(self):
        return re.compile("/{}/?".format(self.prefix))

class PropGet(Route):

    def __init__(self):
        super().__init__("property")
        self.regex = self.path_getter_re()

    def get(self, handler, prop):
        if prop not in ALLOWED_PROPERTIES:
            return handler.json_error(HTTPStatus.BAD_REQUEST, "Property '{}' is not allowed".format(prop))
        mpv = handler.server.mpv
        try:
            val = getattr(mpv, prop.replace("-", "_"))
        except (TimeoutError, MPVError) as e:
            return handler.json_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
        result = {prop: val}
        handler.json_success(result)

class PropSet(Route):

    def __init__(self):
        super().__init__("property")
        self.regex = self.path_setter_re()

    def post(self, handler):
        inp_obj = handler.decode_json_input()
        if inp_obj is None:
            return
        prop = next(iter(inp_obj))
        val = inp_obj[prop]
        if prop not in ALLOWED_PROPERTIES:
            return handler.json_error(HTTPStatus.BAD_REQUEST, "Property '{}' is not allowed".format(prop))
        mpv = handler.server.mpv
        try:
            setattr(mpv, prop.replace("-", "_"), val)
        except (TimeoutError, MPVError) as e:
            return handler.json_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
        handler.json_success()

class CmdRun(Route):

    def __init__(self):
        super().__init__("command")
        self.regex = self.path_setter_re()

    def post(self, handler):
        inp_obj = handler.decode_json_input()
        if inp_obj is None:
            return
        cmdname = inp_obj["cmd"]
        if cmdname not in ALLOWED_COMMANDS:
            return handler.json_error(HTTPStatus.BAD_REQUEST, "Command '{}' is not allowed".format(cmdname))
        mpv = handler.server.mpv
        args = inp_obj["args"]
        try:
            resp = mpv.command("osd-msg-bar", cmdname, *args)
        except (TimeoutError, MPVError) as e:
            return handler.json_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
        handler.json_success(resp)

class MpvRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        path = urlparse.unquote(self.path)
        if not self.find_route(path):
            return self.serve_static_file(STATIC_ROOT, path)

    def do_POST(self):
        path = urlparse.unquote(self.path)
        if not self.find_route(path):
            return self.send_error(HTTPStatus.NOT_FOUND)

    def find_route(self, path):
        routes = self.server.routes
        for route in routes:
            match = route.match(path)
            if match:
                method = self.command.lower()
                if method in ("get", "post") and getattr(route, method, None):
                    run = getattr(route, method, None)
                    run(self, *match.groups())
                else:
                    self.json_error(HTTPStatus.NOT_IMPLEMENTED, "Not supported method: {}".format(self.requestline))
                return True

        return False

    def decode_json_input(self):
        if "content-length" not in self.headers:
            self.json_error(HTTPStatus.BAD_REQUEST, "Content-Length is required")
            return
        content_len = int(self.headers["content-length"])
        content = self.rfile.read(content_len).decode()
        data = json.loads(content)
        return data

    def json_success(self, data=None):
        if data is None:
            resp = b""
        else:
            resp = json.dumps(data).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def json_error(self, status, err_str):
        err = {"error": err_str}
        resp = json.dumps(err).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def serve_static_file(self, root=STATIC_ROOT, path=None):
        if path is None:
            path = urlparse.unquote(self.path)
        root = os.path.join(os.path.abspath(root), '')

        if path.endswith("/"):
            path += "index.html"
        file_path = os.path.normpath(os.path.join(root, path.lstrip('/\\')))

        if not file_path.startswith(root):
            return self.send_error(HTTPStatus.FORBIDDEN)
        if not os.path.isfile(file_path):
            return self.send_error(HTTPStatus.NOT_FOUND)

        self.send_response(HTTPStatus.OK)

        stat = os.stat(file_path)
        self.send_header('Content-Length', str(stat.st_size))

        mimetype, _ = mimetypes.guess_type(file_path)
        if mimetype:
            self.send_header('Content-Type', mimetype)

        self.end_headers()

        with open(file_path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

class MpvServer(ThreadingHTTPServer):

    def __init__(self, mpv, routes, address):
        self.mpv = mpv
        self.routes = routes
        super().__init__(address, MpvRequestHandler)

def main():
    mpv = Mpv(start_mpv=False, ipc_socket=IPC_SOCKET)
    try:
        server = MpvServer(mpv,
                           [PropGet(), PropSet(), CmdRun()],
                           LISTEN)
        server.serve_forever()
    except KeyboardInterrupt:
        mpv.terminate()

if __name__ == "__main__":
    main()
