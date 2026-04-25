import json
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from labrats.personas import (
	delete_persona,
	load_profiles,
	save_persona,
	save_profiles,
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _list_personas(config_dir):
	persona_dir = config_dir / "personas"
	result = []
	for path in sorted(persona_dir.glob("*.yaml")):
		with open(path) as f:
			data = yaml.safe_load(f)
		result.append({
			"stem": path.stem,
			"name": data["name"],
			"role": data["role"],
			"prompt": data["prompt"],
			"scored_fields": data["scored_fields"],
			"model": data.get("model", ""),
			"enabled": data.get("enabled", True),
		})
	return result


class ConfigHandler(BaseHTTPRequestHandler):
	config_dir: Path = Path()

	def log_message(self, fmt, *args):
		pass  # silence request logs

	def _send_json(self, data, status=200):
		body = json.dumps(data).encode()
		self.send_response(status)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def _read_body(self):
		length = int(
			self.headers.get("Content-Length", 0)
		)
		raw = self.rfile.read(length)
		return json.loads(raw) if raw else {}

	def _send_html(self, html):
		body = html.encode()
		self.send_response(200)
		self.send_header("Content-Type", "text/html")
		self.send_header(
			"Content-Length", str(len(body))
		)
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self):
		if self.path == "/api/personas":
			data = _list_personas(self.config_dir)
			self._send_json(data)
		elif self.path == "/api/profiles":
			profiles = load_profiles(self.config_dir)
			self._send_json(profiles)
		elif self.path == "/" or self.path == "":
			env = Environment(
				loader=FileSystemLoader(str(TEMPLATE_DIR)),
				autoescape=True,
			)
			tmpl = env.get_template("config.html.j2")
			html = tmpl.render()
			self._send_html(html)
		else:
			self.send_error(404)

	def do_POST(self):
		if self.path == "/api/personas":
			data = self._read_body()
			save_persona(self.config_dir, data)
			self._send_json(
				_list_personas(self.config_dir)
			)
		elif self.path == "/api/profiles":
			data = self._read_body()
			profiles = load_profiles(self.config_dir)
			profiles.append(data)
			save_profiles(self.config_dir, profiles)
			self._send_json(profiles)
		else:
			self.send_error(404)

	def do_PUT(self):
		parts = self.path.split("/")
		# /api/personas/<stem>
		if (
			len(parts) == 4
			and parts[1] == "api"
			and parts[2] == "personas"
		):
			stem = parts[3]
			data = self._read_body()
			save_persona(self.config_dir, data, stem)
			self._send_json(
				_list_personas(self.config_dir)
			)
		# /api/profiles/<index>
		elif (
			len(parts) == 4
			and parts[1] == "api"
			and parts[2] == "profiles"
		):
			idx = int(parts[3])
			data = self._read_body()
			profiles = load_profiles(self.config_dir)
			if 0 <= idx < len(profiles):
				profiles[idx] = data
				save_profiles(
					self.config_dir, profiles
				)
			self._send_json(profiles)
		else:
			self.send_error(404)

	def do_DELETE(self):
		parts = self.path.split("/")
		if (
			len(parts) == 4
			and parts[1] == "api"
			and parts[2] == "personas"
		):
			stem = parts[3]
			delete_persona(self.config_dir, stem)
			self._send_json(
				_list_personas(self.config_dir)
			)
		elif (
			len(parts) == 4
			and parts[1] == "api"
			and parts[2] == "profiles"
		):
			idx = int(parts[3])
			profiles = load_profiles(self.config_dir)
			if 0 <= idx < len(profiles):
				profiles.pop(idx)
				save_profiles(
					self.config_dir, profiles
				)
			self._send_json(profiles)
		else:
			self.send_error(404)


def serve_config(
	config_dir: Path, port: int = 8484
):
	ConfigHandler.config_dir = config_dir
	server = HTTPServer(
		("127.0.0.1", port), ConfigHandler
	)
	url = f"http://127.0.0.1:{port}"
	print(f"labrats config → {url}")
	print("Press Ctrl+C to stop.")
	webbrowser.open(url)
	try:
		server.serve_forever()
	except KeyboardInterrupt:
		print("\nStopped.")
	finally:
		server.server_close()
