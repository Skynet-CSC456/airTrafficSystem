"""Small loopback HTTP adapter. Domain workflow lives in Application/services."""
import argparse
import json
import mimetypes
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
from src.integration.application import Application, InputError
from src.integration.runtime import Runtime


def make_server(app, host='127.0.0.1', port=8765, frontend=None):
    assets = Path(frontend).resolve() if frontend else None
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def json_response(self, status, value):
            data = json.dumps(value, allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/api/state':
                try:self.json_response(200,app.snapshot())
                except Exception as exc:self.json_response(500,{'error':str(exc)})
                return
            if path.startswith('/api/') or assets is None:
                self.json_response(404,{'error':'Route not found'});return
            file = (assets/unquote(path).lstrip('/')).resolve()
            if not file.is_relative_to(assets):
                self.json_response(404,{'error':'Route not found'});return
            if path == '/': file = assets/'index.html'
            if not file.is_file():
                self.json_response(404,{'error':'Asset not found; build the frontend first'});return
            data=file.read_bytes();self.send_response(200)
            self.send_header('Content-Type',mimetypes.guess_type(file)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

        def do_POST(self):
            self.mutate()

        def do_PATCH(self):
            self.mutate()

        def mutate(self):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 65536:raise InputError('JSON body must be 1–65536 bytes')
                payload=json.loads(self.rfile.read(length))
                if not isinstance(payload,dict):raise InputError('JSON body must be an object')
                with app.mutation(self.headers.get('X-Demo-Generation')):
                    path=urlsplit(self.path).path
                    if self.command=='POST' and path=='/api/reset':
                        if payload:raise InputError('Reset body must be empty')
                        return self.json_response(200,app.reset())
                    if self.command=='POST' and path=='/api/aircraft':
                        return self.json_response(201,app.create_aircraft(payload))
                    if self.command=='PATCH' and path.startswith('/api/aircraft/'):
                        return self.json_response(200,app.update_aircraft(unquote(path[len('/api/aircraft/'):]),payload))
                    if self.command=='POST' and path=='/api/requests':
                        return self.json_response(202,app.request_clearance(payload))
                    if self.command=='POST' and path=='/api/hold':
                        return self.json_response(200,app.hold(payload))
                    if self.command=='POST' and path.startswith('/api/operations/') and path.endswith('/begin'):
                        if set(payload)-{'automatic'} or ('automatic' in payload and type(payload['automatic']) is not bool):raise InputError('Provide an optional automatic boolean')
                        return self.json_response(200,app.begin(unquote(path[len('/api/operations/'):-len('/begin')]),automatic=payload.get('automatic',False)))
                    if self.command=='POST' and path=='/api/runways':
                        return self.json_response(201,app.add_runway(payload))
                    if self.command=='POST' and path.startswith('/api/operations/') and path.endswith('/complete'):
                        if payload:raise InputError('Completion body must be empty')
                        return self.json_response(200,app.complete(unquote(path[len('/api/operations/'):-len('/complete')])))
                    self.json_response(404,{'error':'Route not found'})
            except InputError as exc:self.json_response(exc.status,{'error':str(exc)})
            except KeyError as exc:self.json_response(404,{'error':'Unknown request or missing field: '+str(exc)})
            except (ValueError,TypeError) as exc:self.json_response(400,{'error':str(exc)})
            except sqlite3.IntegrityError:self.json_response(409,{'error':'State already exists or conflicts with an active operation'})
            except Exception as exc:self.json_response(500,{'error':str(exc)})
    return ThreadingHTTPServer((host,port),Handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--frontend',type=Path)
    args=parser.parse_args()
    app=Application(args.database)
    # Hold remains an internal test fixture; the normal UI always runs operations.
    app.hold({'enabled':False})
    runtime=Runtime(app).start()
    server=make_server(app,port=args.port,frontend=args.frontend)
    print(f'Skynet Command: http://127.0.0.1:{server.server_port} | database: {app.database}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();runtime.close()


if __name__=='__main__':main()
