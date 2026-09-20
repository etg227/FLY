"""Core ownership, port-preflight and controller-auth robustness tests."""
import json, socket, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from backend.config import Paths
from backend.core_manager import CoreError, CoreManager, _port_is_free

class PortTests(unittest.TestCase):
    def test_occupied_port_is_detected_and_released_port_recovers(self):
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        sock.bind(("127.0.0.1",0)); sock.listen(1)
        port=sock.getsockname()[1]
        self.assertFalse(_port_is_free(port))
        cm=CoreManager(Paths(Path(tempfile.mkdtemp())),lambda m:None)
        with self.assertRaises(CoreError):
            cm._preflight_ports({"mixed_port":port,"controller_port":port+1})
        sock.close()
        self.assertTrue(_port_is_free(port))

class _Handler(BaseHTTPRequestHandler):
    secret="test-secret"
    def do_GET(self):
        if self.path!="/version":
            self.send_response(404); self.end_headers(); return
        if self.headers.get("Authorization")!=f"Bearer {self.secret}":
            self.send_response(401); self.end_headers(); return
        body=json.dumps({"version":"test"}).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass

class ControllerAuthTests(unittest.TestCase):
    def setUp(self):
        self.server=HTTPServer(("127.0.0.1",0),_Handler)
        self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.cm=CoreManager(Paths(Path(tempfile.mkdtemp())),lambda m:None)
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def test_controller_requires_expected_secret(self):
        self.assertTrue(self.cm._controller_ready(self.port,"test-secret"))
        self.assertFalse(self.cm._controller_ready(self.port,"wrong-secret"))
        self.assertFalse(self.cm._controller_ready(self.port,""))

if __name__=="__main__":
    unittest.main()
