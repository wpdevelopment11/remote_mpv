#!/usr/bin/python3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import mimetypes
import multiprocessing.connection
import os
import re
import shutil
import socket
from urllib import parse as urlparse

LISTEN = ("127.0.0.1", 7271)

IPC_SOCKET = ("\\\\.\\pipe\\" if os.name == "nt" else "/tmp/") + "mpvsocket"
STATIC_ROOT = "static"

ALLOWED_PROPERTIES = [
    "mute",
    "pause",
    "playlist",
    "speed",
    "volume-max",
    "volume",
]

ALLOWED_COMMANDS = [
    "add",
    "multiply",
    "playlist-play-index",
    "seek",
]

# Show changes on OSD
OSD_PROPERTIES = ["mute", "volume"]

class MpvError(Exception): pass
class MpvNotAllowed(Exception): pass

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
            raise MpvNotAllowed("Property '{}' is not allowed".format(prop))
        val = handler.mpv_command(["get_property", prop])
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
            raise MpvNotAllowed("Property '{}' is not allowed".format(prop))
        if prop in OSD_PROPERTIES:
            cmd = ["osd-msg-bar", "set"]
            if isinstance(val, bool):
                val = "yes" if val is True else "no"
            else:
                val = str(val)
        else:
            cmd = ["set_property"]

        cmd += [prop, val]

        handler.mpv_command(cmd)
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
            raise MpvNotAllowed("Command '{}' is not allowed".format(cmdname))
        args = inp_obj["args"]
        resp = handler.mpv_command(["osd-msg-bar", cmdname, *args])
        handler.json_success(resp)

class UnixSockConnection:

    def __init__(self, path):
        self._path = path
        self._messages = []

        self._client = socket.socket(socket.AF_UNIX)
        self._client.connect(self._path)

    def send_bytes(self, b):
        self._client.send(b)

    def recv_bytes(self):
        if not self._messages:
            client = self._client
            buffer = bytearray()
            while True:
                recv = client.recv(16384)
                if not recv:
                    raise EOFError("Disconnected from mpv")
                buffer.extend(recv)
                if buffer[-1] == 10:
                    break
            self._messages = buffer.split(b"\n")[:-1]

        return self._messages.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._client.__exit__(exc_type, exc_value, traceback)

class MpvRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    mpv_conn = None

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
                if getattr(route, method, None):
                    try:
                        mpv_conn = (multiprocessing.connection.Client(self.server.mpv_sock_path)
                                    if os.name == "nt"
                                    else UnixSockConnection(self.server.mpv_sock_path))
                    except (FileNotFoundError, ConnectionRefusedError) as e:
                        self.json_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Can't connect to mpv ipc server")
                        return True
                    with mpv_conn:
                        self.mpv_conn = mpv_conn
                        try:
                            run = getattr(route, method, None)
                            run(self, *match.groups())
                        except (MpvError, MpvNotAllowed) as e:
                            self.json_error(HTTPStatus.BAD_REQUEST, str(e))
                        except EOFError as e:
                            self.json_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
                        finally:
                            self.mpv_conn = None
                else:
                    self.json_error(HTTPStatus.NOT_IMPLEMENTED, "Not supported method: {}".format(self.requestline))
                return True

        return False

    def decode_json_input(self):
        if "content-length" not in self.headers:
            self.json_error(HTTPStatus.BAD_REQUEST, "Content-Length is required")
            return
        content_len = int(self.headers["content-length"])
        content = self.rfile.read(content_len)
        data = json.loads(content.decode())
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
        self.send_header("Connection", "close")
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

    def mpv_command(self, command):
        cmd = json.dumps({"command": command})
        self.mpv_conn.send_bytes(cmd.encode() + b"\n")

        while True:
            resp_bytes = self.mpv_conn.recv_bytes()
            resp = json.loads(resp_bytes)
            # Don't care about events
            if "event" not in resp:
                break

        if resp["error"] != "success":
            raise MpvError(resp["error"])

        return resp.get("data", resp["error"])

class MpvServer(ThreadingHTTPServer):

    def __init__(self, mpv_sock_path, routes, address):
        self.mpv_sock_path = mpv_sock_path
        self.routes = routes
        super().__init__(address, MpvRequestHandler)

def main():
    try:
        server = MpvServer(IPC_SOCKET,
                           [PropGet(), PropSet(), CmdRun()],
                           LISTEN)
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
