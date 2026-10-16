"""Character-aware long-term memory integration.

Connects to a CharacterMemory server (custom or managed locally) and exposes
it as a Newelle long-term memory handler. CharacterMemory and its server run
in a separate environment; no Newelle source patch is needed.
"""
import atexit
import contextvars
import functools
import gettext
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import requests
from gi.repository import Adw, Gio, GLib, Gtk

from ..extensions import NewelleExtension
from ..handlers import ErrorSeverity, ExtraSettings, TabButtonDescription
from ..handlers.memory import MemoryHandler
from ..utility.strings import clean_prompt, remove_thinking_blocks
from ..utility.system import get_spawn_command

_ = gettext.gettext
_TURN = contextvars.ContextVar("character_memory_turn", default=None)
_TOOL_RUN = contextvars.ContextVar("character_memory_tool_run", default=None)
_MARKER = "_character_memory_turn"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(temporary, "x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class MemoryHTTPError(RuntimeError):
    def __init__(self, status, path):
        self.status = status
        super().__init__(_("Memory server returned HTTP %(status)s for %(path)s") %
                         {"status": status, "path": path.split("?")[0]})


def _request(config, method, path, payload=None, timeout=120):
    # No retries: /context and /save have side effects, including on some 500s.
    try:
        response = requests.request(method, config["url"] + path,
                                    json=payload,
                                    headers={"X-API-Key": config["api_key"]},
                                    timeout=(10, timeout), allow_redirects=False)
    except requests.RequestException as error:
        raise RuntimeError(_("Memory server request failed. It may have reached the server; it was not retried.")) from error
    if not 200 <= response.status_code < 300:
        raise MemoryHTTPError(response.status_code, path)
    try:
        return response.json()
    except ValueError as error:
        raise RuntimeError(_("Memory server returned invalid JSON.")) from error


class ConversationStore:
    """Atomic metadata only: never duplicate message content or credentials here."""
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.locks = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {"chats": {}, "turns": {}}

    def conversation_lock(self, key):
        with self.lock:
            return self.locks.setdefault(key, threading.RLock())

    def read(self, table, key):
        with self.lock:
            return dict(self.data[table].get(key, {}))

    def write(self, table, key, value):
        with self.lock:
            self.data[table][key] = value
            _atomic_json(self.path, self.data)

    def reset(self, key):
        with self.conversation_lock(key):
            self.write("chats", key, {})


def open_memory_app(extension, graph):
    """Open the CharacterMemory web app (browser or knowledge graph) in a tab.

    Starts the managed server when needed: connection() boots it on the
    worker thread below, and the tab opens once it is reachable.
    """
    if (extension.option("instance_mode", "managed") == "managed"
            and not extension.runtime.is_running):
        extension.report(_("Starting the memory server…"))
    extension.work(extension.connection,
                   lambda config: _memory_app_tab(extension, config, graph))


def _rgba_hex(color):
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, round(color.red * 255))),
        max(0, min(255, round(color.green * 255))),
        max(0, min(255, round(color.blue * 255))))


def _graph_embed_params(widget):
    """Embed parameters restyling the live graph to Newelle's GTK theme.

    Neutral elements (background, ink, labels, muted nodes and edges) follow
    the GTK theme; semantic node and edge colors keep the server palette so
    the graph stays readable.
    """
    params = {"theme": "dark" if Adw.StyleManager.get_default().get_dark() else "light"}
    context = widget.get_style_context()
    for param, named in (("color_bg", "theme_bg_color"),
                         ("color_label", "theme_fg_color"),
                         ("color_label_shadow", "theme_bg_color"),
                         ("color_ink", "theme_fg_color"),
                         ("color_muted", "insensitive_fg_color"),
                         ("color_muted_edge", "borders")):
        found, color = context.lookup_color(named)
        if found:
            params[param] = _rgba_hex(color)[1:]
    return params


def _memory_app_tab(extension, config, graph):
    from gi.repository import WebKit

    from ..ui.widgets.browser import BrowserWidget
    path = "/gui/embed/knowledge-graph" if graph else "/gui"
    # Start blank so the document-start script is installed before navigation.
    browser = BrowserWidget(starting_url="about:blank")
    script = (f"if(location.origin === new URL({json.dumps(config['url'])}).origin)"
              f"{{localStorage.setItem('cm_api_key', {json.dumps(config['api_key'])});}}")
    browser.user_content_manager.add_script(WebKit.UserScript.new(
        script, WebKit.UserContentInjectedFrames.TOP_FRAME,
        WebKit.UserScriptInjectionTime.START, None, None))
    params = {"character": config["character"], "user": config["user"]}
    if graph:
        params.update(_graph_embed_params(browser))
    url = config["url"] + path + "?" + urlencode(params)
    browser.starting_url = url
    browser.navigate_to(url)
    tab = extension.ui_controller.add_tab(browser)
    tab.set_title(_("Live Knowledge Graph") if graph else _("CharacterMemory"))
    tab.set_icon(Gio.ThemedIcon.new("network-server-symbolic"))
    if graph:
        extension.track_graph_tab(browser, config)
    return False


class CharacterMemoryHandler(MemoryHandler):
    key = "character_memory"
    extension = None  # Bound on a per-extension subclass, never a module singleton.

    def get_extra_settings(self):
        if not self.extension.setup_complete():
            return [ExtraSettings.ButtonSetting(
                "configure", _("CharacterMemory needs setup"),
                _("Install the memory server and create a character, guided."),
                self.extension.open_setup, label=_("Start setup"))]
        return [ExtraSettings.ButtonSetting(
            "configure", _("CharacterMemory setup"),
            _("Review the server, character, and user in the extension settings."),
            self.extension.open_setup, label=_("Open setup"))]

    def add_tab_menu_entries(self):
        return [TabButtonDescription(_("CharacterMemory"), "network-server-symbolic",
                                     lambda *_args: open_memory_app(self.extension, False)),
                TabButtonDescription(_("Live Knowledge Graph"), "view-grid-symbolic",
                                     lambda *_args: open_memory_app(self.extension, True))]

    def get_context(self, prompt, history):
        turn = _TURN.get()
        if turn is None:
            self.extension.report(_("Cannot determine the originating chat. Re-enable the CharacterMemory extension."))
            return []
        if turn.get("failed"):
            return []
        return self.extension.queue(turn["key"]).submit(self.extension.recall, turn).result()

    def register_response(self, bot_response, history):
        token = next((m.get(_MARKER) for m in reversed(history)
                      if m.get("User") == "User" and m.get(_MARKER)), None)
        if token:
            turn = self.extension.turns.get(token)
            if turn:
                self.extension.queue(turn["key"]).submit(
                    self.extension.save_answer, token, str(bot_response))


# Written to the private runtime directory at startup. Subclassing the exported
# agent before importing server modules also covers admin-created/reloaded agents.
_MANAGED_LAUNCHER = r'''
import json, os, threading, urllib.request, sys, fcntl
from pathlib import Path
settings = json.loads(Path(__file__).with_name("launch.json").read_text())
server = None
def watch_parent():
    sys.stdin.buffer.read()
    if server is None:
        os._exit(0)
    server.should_exit = True
threading.Thread(target=watch_parent, daemon=True).start()
# Do not let two Newelle processes open the same managed SQLite databases.
instance_lock = open(Path(__file__).with_name("instance.lock"), "a")
fcntl.flock(instance_lock, fcntl.LOCK_EX)
for key, value in settings["environment"].items():
    os.environ[key] = value
bridge = settings["bridge"]
import numpy as np
import character_memory
from character_memory.llm.embedding_base import EmbeddingProvider
from character_memory.llm.openai_client import OpenAICompatibleLLM
from character_memory.config import LLMConfig
def rpc(path, body):
    request = urllib.request.Request(bridge + path, json.dumps(body).encode(),
        {"Content-Type": "application/json", "Authorization": "Bearer " + settings["token"]})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)
class NewelleEmbeddings(EmbeddingProvider):
    @property
    def dim(self):
        return settings["dimension"]
    @property
    def index_fingerprint(self):
        return settings["fingerprint"]
    def embed(self, texts):
        return self._embed(texts, None)
    def embed_queries(self, texts):
        return self._embed(texts, "query")
    def embed_documents(self, texts):
        return self._embed(texts, "document")
    def _embed(self, texts, purpose):
        texts = [texts] if isinstance(texts, str) else list(texts)
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        result = np.asarray(rpc("/embeddings", {"input": texts, "purpose": purpose})["vectors"], dtype=np.float32)
        if result.shape != (len(texts), self.dim):
            raise ValueError("Newelle embedding dimension changed; restart the managed instance")
        return result
class NewelleAgent(character_memory.CharacterAgent):
    def load_from_config(self, *args, **kwargs):
        kwargs["llm"] = OpenAICompatibleLLM(LLMConfig(base_url=bridge + "/v1",
            api_key=settings["token"], model="newelle", timeout=300))
        kwargs["embedder"] = NewelleEmbeddings()
        return super().load_from_config(*args, **kwargs)
    def _configure_temporal_resolution(self, config):
        if config is not None and config.engine == "llm":
            from character_memory.temporal import LLMTemporalResolutionEngine
            self.temporal_resolution_engine = LLMTemporalResolutionEngine(self.llm,
                max_tokens=config.llm.max_tokens) if config.enabled else None
            return
        return super()._configure_temporal_resolution(config)
character_memory.CharacterAgent = NewelleAgent
import uvicorn
from character_memory.server.api import app
@app.get("/_newelle/ready")
def ready():
    return {"nonce": settings["nonce"]}
# Parent holds stdin open; EOF works across flatpak-spawn as well.
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings["port"],
    access_log=False, log_level="warning"))
server.run()
'''


class ProviderBridge:
    """Narrow RPC surface; calls providers directly, never the chat controller."""
    def __init__(self, llm, embedding):
        self.llm, self.embedding = llm, embedding
        self.token = secrets.token_urlsafe(32)
        self.llm_lock = threading.Lock()
        self.embedding_lock = threading.Lock()
        bridge = self

        class RequestHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if not hmac.compare_digest(self.headers.get("Authorization", ""),
                                           "Bearer " + bridge.token):
                    self.send_error(401)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8 * 1024 * 1024:
                        self.send_error(413)
                        return
                    body = json.loads(self.rfile.read(length))
                    result = bridge.dispatch(self.path, body)
                    data = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception:  # noqa: BLE001 - translate untrusted provider failures to HTTP errors
                    # Provider exceptions may contain credentials or source text.
                    self.send_error(502, "Newelle provider failed")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
        self.server.daemon_threads = True
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def dispatch(self, path, body):
        if path == "/embeddings":
            texts = body["input"]
            purpose = body.get("purpose")
            if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
                raise ValueError("Invalid embedding input")
            if purpose not in (None, "query", "document"):
                raise ValueError("Invalid purpose")
            with self.embedding_lock:
                result = self.embedding.get_embedding(texts, purpose=purpose)
            return {"vectors": result.tolist()}
        if path != "/v1/chat/completions" or body.get("stream"):
            raise ValueError("Unsupported bridge request")
        systems, history = [], []
        for message in body["messages"]:
            role, content = message["role"], message["content"]
            if not isinstance(content, str):
                raise TypeError("Only text is supported")
            if role in ("system", "developer"):
                systems.append(content)
            elif role in ("user", "assistant"):
                history.append({"User": "User" if role == "user" else "Assistant", "Message": content})
            else:
                raise ValueError("Unsupported message role")
        prompt = history.pop()["Message"] if history and history[-1]["User"] == "User" else ""
        with self.llm_lock:
            answer = str(self.llm.generate_text(prompt, history, systems))
        return {"id": "newelle-" + uuid.uuid4().hex, "object": "chat.completion",
                "created": int(time.time()), "model": "newelle", "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": answer},
                     "finish_reason": "stop"}]}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ManagedInstance:
    def __init__(self, extension):
        self.extension = extension
        self.root = extension.root / "managed"
        self.process = None
        self.bridge = None
        self.fingerprint = None
        self.lock = threading.RLock()
        self.starting = threading.Event()
        self.status = _("Stopped")

    @property
    def python(self):
        return str(self.root / "venv" / "bin" / "python")

    @property
    def installed(self):
        return (self.root / "venv" / "bin" / "python").exists()

    @property
    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def install(self):
        with self.lock:
            if self.extension.closed:
                raise RuntimeError(_("Enable the CharacterMemory extension before installing its server."))
            self.stop()
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.status = _("Installing")
            prefix = get_spawn_command()
            source = self.extension.option("install_source", "").strip()
            package = str(Path(source).expanduser().resolve()) + "[server]" if source else "charactermemory[server]"
            commands = [prefix + ["python3", "-m", "venv", str(self.root / "venv")],
                        prefix + [self.python, "-m", "pip", "install", "--upgrade", package]]
            log = self.root / "install.log"
            with open(log, "w") as output:
                os.chmod(log, 0o600)
                for command in commands:
                    result = subprocess.run(command, stdout=output, stderr=output, timeout=1200, check=False)
                    if result.returncode:
                        self.status = _("Installation failed")
                        raise RuntimeError(_("Installation failed. Check installation logs, host Python with venv, and Flatpak host-command permission."))
            self.status = _("Installed; ready to start")

    def _providers(self):
        ext = self.extension
        llm, embedding = getattr(ext, "llm", None), getattr(ext, "embedding", None)
        if llm is None or embedding is None:
            raise RuntimeError(_("Select and configure a Newelle LLM and embedding provider first."))
        # Hash settings (including prefix/model changes), but never write their contents.
        settings = ext.settings.get_string("embedding-settings")
        fingerprint = _digest([embedding.key, settings])
        return llm, embedding, fingerprint

    def start(self):
        with self.lock:
            if self.extension.closed:
                raise RuntimeError(_("Enable the CharacterMemory extension before starting its server."))
            llm, embedding, fingerprint = self._providers()
            if self.process and self.process.poll() is None and self.fingerprint == fingerprint:
                self.bridge.llm = llm
                return self.config
            self.stop()
            if not self.installed:
                raise RuntimeError(_("Install the managed server from CharacterMemory setup first."))
            self.status = _("Starting")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            for directory in ("assets", "data"):
                (self.root / directory).mkdir(exist_ok=True)
            key_path = self.root / "api-key.json"
            if not key_path.exists():
                _atomic_json(key_path, {"key": secrets.token_urlsafe(32)})
            api_key = json.loads(key_path.read_text())["key"]
            # Probe live vectors: Newelle's dimension property can cache an old model.
            dimension = int(embedding.get_embedding(["dimension probe"]).shape[1])
            self.bridge = ProviderBridge(llm, embedding)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                default_port = sock.getsockname()[1]
            port_path = self.root / "port.json"
            if not port_path.exists():
                _atomic_json(port_path, {"port": default_port})
            port = json.loads(port_path.read_text())["port"]
            environment = {"CM_ASSETS_DIR": str(self.root / "assets"),
                           "CM_SAVE_DIR": str(self.root / "data"),
                           "CM_STORAGE_BACKEND": "sqlite", "CM_RETRIEVAL_BACKEND": "hybrid",
                           "CM_API_KEY": api_key, "CM_SYNC_INTERVAL": "0"}
            nonce = secrets.token_hex(16)
            launch = {"nonce": nonce, "bridge": self.bridge.url, "token": self.bridge.token,
                      "port": port, "dimension": dimension, "fingerprint": fingerprint,
                      "environment": environment}
            _atomic_json(self.root / "launch.json", launch)
            launcher = self.root / "server.py"
            launcher.write_text(_MANAGED_LAUNCHER)
            self.config = {"url": "http://127.0.0.1:" + str(port), "api_key": api_key,
                           "instance": "managed"}
            try:
                with open(self.root / "server.log", "w") as output:
                    os.chmod(self.root / "server.log", 0o600)
                    self.process = subprocess.Popen(get_spawn_command() + [self.python, str(launcher)],
                        stdin=subprocess.PIPE, stdout=output, stderr=output)
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise RuntimeError(_("Managed server exited. Check its logs."))
                    try:
                        ready = _request(self.config, "GET", "/_newelle/ready", timeout=2)
                        if ready.get("nonce") != nonce:
                            time.sleep(0.25)
                            continue
                        self.fingerprint = fingerprint
                        self.status = _("Running")
                        return self.config
                    except (RuntimeError, MemoryHTTPError):
                        time.sleep(0.25)
                raise RuntimeError(_("Managed server startup timed out. Check its logs."))
            except Exception:
                self.stop()
                self.status = _("Startup failed")
                raise

    def start_async(self):
        """Return the running config, or kick a background start and return None.

        Unlike start(), this never blocks. Callers that can proceed without
        the server (e.g. the chat path) skip their turn while it boots; the
        boot thread reports failures through the extension.
        """
        if self.is_running:
            return self.config
        extension = self.extension
        if not self.starting.is_set():
            self.starting.set()
            def boot():
                try:
                    self.start()
                except Exception as error:  # noqa: BLE001 - reported at the extension boundary
                    extension.report(str(error))
                finally:
                    self.starting.clear()
            threading.Thread(target=boot, daemon=True,
                             name="character-memory-autostart").start()
        return None

    def stop(self):
        with self.lock:
            if self.process:
                if self.process.stdin:
                    self.process.stdin.close()
                try:
                    self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
                self.process = None
            if self.bridge:
                self.bridge.close()
                self.bridge = None
            self.status = _("Stopped")


class CharacterMemoryExtension(NewelleExtension):
    name = "Character Memory"
    id = "character_memory"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)
        self.error_func = None
        self.root = Path(GLib.get_user_data_dir()).resolve() / "newelle" / "extensions" / self.id
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.store = ConversationStore(self.root / "conversations.json")
        self.runtime = ManagedInstance(self)
        self.handler_class = type("BoundCharacterMemoryHandler", (CharacterMemoryHandler,), {"extension": self})
        self.turns = {}
        self.cache = {}
        self.queues = {}
        self.state_lock = threading.RLock()
        self.adapters = []
        self.controller = None
        self.closed = False
        self._watch = None
        self._shutdown_signal = None
        self._graph_tabs = []
        self._theme_watch = None
        atexit.register(self.runtime.stop)

    def option(self, key, default):
        value = self.get_setting(key, False)
        return default if value is None else value

    def queue(self, key):
        with self.state_lock:
            if key not in self.queues:
                self.queues[key] = ThreadPoolExecutor(max_workers=1,
                    thread_name_prefix="character-memory")
            return self.queues[key]

    def report(self, message):
        def notify():
            if self.error_func:
                self.throw(str(message), ErrorSeverity.WARNING)
            elif getattr(self, "ui_controller", None):
                self.ui_controller.send_notification(str(message))
            return False
        GLib.idle_add(notify)

    def work(self, function, done=None):
        def run():
            try:
                result = function()
                if done:
                    def finish():
                        try:
                            done(result)
                        except Exception as error:  # noqa: BLE001 - report failures at the extension boundary
                            self.report(str(error))
                        return False
                    GLib.idle_add(finish)
            except Exception as error:  # noqa: BLE001 - report failures at the extension boundary
                self.report(str(error))
        threading.Thread(target=run, daemon=True).start()

    def get_memory_handlers(self):
        return [{"key": CharacterMemoryHandler.key, "title": _("CharacterMemory"),
                 "description": _("Advanced memory system for AI characters handling lore, personality, emotions and memory in a human way"),
                 "website": "https://github.com/FrancescoCaracciolo/CharacterMemory",
                 "class": self.handler_class}]

    def setup_complete(self):
        """A server is ready to use: installed locally, or a custom URL is configured."""
        if self.option("instance_mode", "managed") == "custom":
            return bool(str(self.option("server_url", "")).strip())
        return self.runtime.installed

    def get_extra_settings(self):
        if not self.setup_complete():
            return [ExtraSettings.ButtonSetting(
                "setup", _("CharacterMemory needs setup"),
                _("Install the local memory server and create your character, step by step."),
                self.open_setup, label=_("Start setup"))]
        managed = self.option("instance_mode", "managed") == "managed"
        settings = [ExtraSettings.ComboSetting(
            "instance_mode", _("Instance"), _("Choose where memory is stored."),
            ((_("Local managed server"), "managed"), (_("Custom server"), "custom")),
            "managed", update_settings=True)]
        if not managed:
            settings.extend([
                ExtraSettings.EntrySetting("server_url", _("Custom server URL"), "", "http://localhost:8000"),
                ExtraSettings.EntrySetting("api_key", _("Custom server API key"), "", "", password=True),
            ])
        settings.extend([
            ExtraSettings.EntrySetting("character", _("Character"), _("Select or create a character in setup."), ""),
            ExtraSettings.EntrySetting("user", _("User ID"), "", "user"),
            ExtraSettings.ButtonSetting("setup", _("Setup and character creation"),
                _("Manage the local instance, discover characters, and open memory apps."),
                self.open_setup, label=_("Open setup")),
        ])
        if managed:
            settings.append(ExtraSettings.EntrySetting(
                "install_source", _("Local source directory (optional)"),
                _("Leave empty to install charactermemory[server] from PyPI."), ""))
        settings.append(ExtraSettings.ButtonSetting(
            "reset", _("Reset memory conversation"),
            _("Start a new server chat for the current Newelle chat; keep learned memories."),
            self.reset_conversation, label=_("Reset")))
        return settings

    def connection(self, require_character=True, wait=True):
        if self.closed:
            raise RuntimeError(_("The CharacterMemory extension is disabled."))
        mode = self.option("instance_mode", "managed")
        if mode == "managed":
            if wait:
                config = dict(self.runtime.start())
            else:
                # Chat path: never block the response on a server boot.
                ready = self.runtime.start_async()
                if ready is None:
                    raise RuntimeError(_("The memory server is still starting. It will join the conversation from the next message."))
                config = dict(ready)
        else:
            url = str(self.option("server_url", "http://localhost:8000")).strip().rstrip("/")
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise RuntimeError(_("Enter an HTTP(S) server URL without credentials, query, or fragment."))
            config = {"url": url, "api_key": str(self.option("api_key", "") or os.getenv("CM_API_KEY", "")),
                      "instance": url}
        config.update(character=str(self.option("character", "")).strip(),
                      user=str(self.option("user", "user")).strip() or "user")
        if require_character and not config["character"]:
            raise RuntimeError(_("Select or create a character in CharacterMemory setup first."))
        return config

    def set_ui_controller(self, ui_controller):
        super().set_ui_controller(ui_controller)
        controller = getattr(ui_controller, "controller", None)
        if controller is None:
            controller = getattr(getattr(ui_controller, "window", None), "controller", None)
        if controller is None:
            self.report(_("This Newelle version does not expose a chat controller."))
            return
        self.bind_controller(controller)
        if self._watch is None:
            self._watch = GLib.timeout_add_seconds(1, self._check_lifecycle)
        app = Gio.Application.get_default()
        if app and self._shutdown_signal is None:
            self._shutdown_signal = (app, app.connect("shutdown", lambda *_: self.close()))

    def set_handlers(self, *args, **kwargs):
        super().set_handlers(*args, **kwargs)
        if self.runtime.is_running:
            # Refresh provider references and restart when embedding identity changed.
            self.work(self.runtime.start)
        else:
            # Handler selection changed (e.g. CharacterMemory became the active
            # memory): bring the managed server up in the background.
            self.autostart()

    def _enabled_in_loaders(self):
        """CharacterMemory ships as an integration, so it lives on the
        integrations loader; a copy installed as a user extension would
        register with the extension loader instead."""
        loaders = [getattr(self.controller, name, None)
                   for name in ("integrationsloader", "extensionloader")]
        known = [loader for loader in loaders if loader is not None]
        if not known:
            return True
        return any(self in loader.get_enabled_extensions() for loader in known)

    def _check_lifecycle(self):
        if self.closed:
            self._watch = None
            return False
        if not self._enabled_in_loaders():
            self.close()
            self._watch = None
            return False
        return True

    def active(self):
        return (not self.closed and self.controller is not None
                and self._enabled_in_loaders()
                and self.controller.newelle_settings.memory_on
                and isinstance(self.controller.handlers.memory, self.handler_class))

    def autostart(self):
        """Start the managed server in the background once it can be used.

        Called when the extension binds to a controller and whenever the
        selected handlers change; connection() still starts it on demand.
        """
        if (self.closed or not self.active()
                or self.option("instance_mode", "managed") != "managed"
                or not self.runtime.installed or self.runtime.is_running):
            return
        self.runtime.start_async()

    def bind_controller(self, controller):
        if self.controller is controller and self.adapters:
            return
        previous = getattr(controller, "_character_memory_adapter", None)
        if previous is not None and previous is not self:
            previous.close()
        self.controller = controller
        self.closed = False
        controller._character_memory_adapter = self
        original_context = controller.get_memory_prompt
        original_update = controller.update_memory

        @functools.wraps(original_context)
        def context(chat=None, chat_id=None):
            if not self.active():
                return original_context(chat=chat, chat_id=chat_id)
            local_id = chat_id if chat_id is not None else controller.newelle_settings.chat_id
            messages = chat if chat is not None else controller.get_chat_by_id(local_id)
            try:
                run = _TOOL_RUN.get()
                tool_input = bool(messages and (messages[-1].get("ToolContext") or messages[-1].get("User") == "Console"))
                if run and run["chat_id"] == local_id and run.get("turn"):
                    turn = run["turn"]
                elif tool_input:
                    token = next((m.get(_MARKER) for m in reversed(messages)
                                  if m.get("User") == "User" and not m.get("ToolContext")), None)
                    turn = self.turns.get(token, {"failed": True})
                else:
                    turn = self.capture_turn(local_id, messages)
                    if run and run["chat_id"] == local_id:
                        run["turn"] = turn
                if tool_input:
                    turn = {**turn, "retrieval_message": str(messages[-1]["Message"])}
            except Exception as error:  # noqa: BLE001 - report failures at the extension boundary
                self.report(str(error))
                turn = {"failed": True}
            binding = _TURN.set(turn)
            try:
                # Pass the actual originating chat even for live tool-loop rebuilds.
                return original_context(chat=messages, chat_id=local_id)
            finally:
                _TURN.reset(binding)

        @functools.wraps(original_update)
        def update(bot_response, chat=None):
            if not self.active():
                return original_update(bot_response, chat=chat)
            messages = list(chat if chat is not None else controller.chat)
            # Resolve the token synchronously, before launching a save worker.
            token = next((m.get(_MARKER) for m in reversed(messages)
                          if m.get("User") == "User" and m.get(_MARKER)), None)
            if token and token in self.turns:
                turn = self.turns[token]
                self.queue(turn["key"]).submit(self.save_answer, token, str(bot_response))

        replacements = [("get_memory_prompt", context), ("update_memory", update)]
        original_tools = getattr(controller, "run_llm_with_tools", None)
        if original_tools is not None:
            @functools.wraps(original_tools)
            def run_tools(message, chat_id, *args, **kwargs):
                if not self.active():
                    return original_tools(message, chat_id, *args, **kwargs)
                run = {"chat_id": chat_id, "turn": None}
                binding = _TOOL_RUN.set(run)
                try:
                    answer = original_tools(message, chat_id, *args, **kwargs)
                    # Newelle's tool loop returns its final text without update_memory.
                    # Never persist intermediate tool text or synthetic ToolContext turns.
                    turn = run["turn"]
                    if turn and not turn.get("failed"):
                        self.queue(turn["key"]).submit(self.save_answer, turn["token"], str(answer))
                    return answer
                finally:
                    _TOOL_RUN.reset(binding)
            replacements.append(("run_llm_with_tools", run_tools))

        for name, replacement in replacements:
            self.adapters.append((name, getattr(controller, name), replacement))
            setattr(controller, name, replacement)
        self.autostart()

    def chat_identity(self, local_id):
        chat = getattr(self.controller, "chats", {}).get(local_id)
        return str(chat.get("id") or local_id) if isinstance(chat, dict) else str(local_id)

    def capture_turn(self, local_id, messages):
        message = next((m for m in reversed(messages)
                        if m.get("User") == "User" and not m.get("ToolContext")), None)
        if message is None:
            return {"failed": True}
        with self.state_lock:
            old_token = message.get(_MARKER)
            old = self.turns.get(old_token)
        if old and old["message"] == message["Message"] and old["local_id"] == str(local_id):
            # Drain previously queued saves before deciding whether this is a regeneration.
            self.queue(old["key"]).submit(lambda: None).result()
            if self.store.read("turns", old_token).get("status") != "saved":
                return old
        config = self.connection(wait=False)
        key = _digest([config["instance"], config["character"], config["user"], self.chat_identity(local_id)])
        with self.store.conversation_lock(key):
            current = self.turns.get(message.get(_MARKER))
            if (current and current["key"] == key and current["message"] == message["Message"]
                    and self.store.read("turns", current["token"]).get("status") != "saved"):
                return current
            previous = self.store.read("turns", old_token)
            if old is None and previous and previous.get("status") not in ("saved", "reset", "deferred"):
                self.report(_("This turn was interrupted before memory saving completed. Reset the memory conversation before regenerating it."))
                return {"failed": True}
            # The same user message after a completed or still-buffered save is a
            # regeneration; either way the server conversation restarts from it.
            if old_token and previous.get("status") in ("saved", "deferred"):
                if previous.get("status") == "deferred":
                    self.store.write("turns", old_token, {**previous, "status": "reset"})
                self.store.reset(key)
            token = uuid.uuid4().hex
            message[_MARKER] = token
            turn = {"token": token, "key": key, "local_id": str(local_id),
                    "config": config, "message": str(message["Message"])}
            with self.state_lock:
                self.turns[token] = turn
            return turn

    def recall(self, turn):
        if turn.get("failed"):
            return []
        key, token, config = turn["key"], turn["token"], turn["config"]
        with self.store.conversation_lock(key):
            if "retrieval_message" in turn:
                chat = self.store.read("chats", key)
                if not chat.get("chat_id") or chat.get("uncertain"):
                    return []
                try:
                    result = _request(config, "POST", "/context", {
                        "character": config["character"], "user": config["user"],
                        "chat_id": chat["chat_id"], "save": False,
                        "message": clean_prompt(turn["retrieval_message"]).strip(),
                    })
                    text = result["context_text"]
                    if not isinstance(text, str):
                        raise ValueError(_("Invalid /context response."))
                    return [text] if text.strip() else []
                except Exception as error:
                    self.report(str(error))
                    return []
            record = self.store.read("turns", token)
            if record:
                return self.cache.get(token, [])
            chat = self.store.read("chats", key)
            if chat.get("uncertain"):
                self.report(_("The previous memory request has an unknown outcome. Reset this memory conversation to continue."))
                return []
            self.store.write("turns", token, {"status": "context_pending", "key": key})
            self.store.write("chats", key, {**chat, "uncertain": True})
            # Newelle prompts embed reasoning traces and attachment blocks; the
            # server stores this message verbatim as the conversation turn.
            payload = {"character": config["character"], "user": config["user"],
                       "message": clean_prompt(turn["message"]).strip()}
            if chat.get("chat_id"):
                payload["chat_id"] = chat["chat_id"]
            try:
                try:
                    result = _request(config, "POST", "/context", payload)
                except MemoryHTTPError as error:
                    if error.status != 404 or "chat_id" not in payload:
                        raise
                    # Confirm the character exists: only missing chats are reset.
                    characters = _request(config, "GET", "/")["characters"]
                    if config["character"] not in characters:
                        raise
                    payload.pop("chat_id")
                    result = _request(config, "POST", "/context", payload)
                chat_id, text = result["chat_id"], result["context_text"]
                if not isinstance(chat_id, str) or not chat_id or not isinstance(text, str):
                    raise ValueError(_("Invalid /context response."))
                self.store.write("chats", key, {"chat_id": chat_id,
                                                "pending": chat.get("pending", [])})
                self.store.write("turns", token, {"status": "recalled", "chat_id": chat_id, "key": key})
                self.cache[token] = [text] if text.strip() else []
                return self.cache[token]
            except Exception as error:  # noqa: BLE001 - report failures at the extension boundary
                self.store.write("turns", token, {"status": "context_failed", "key": key})
                self.report(str(error))
                return []

    def save_answer(self, token, answer):
        turn = self.turns.get(token)
        if not turn:
            return
        # Every save path funnels through here and /save posts the stored
        # record verbatim, so strip the model's reasoning traces once.
        answer = remove_thinking_blocks(str(answer)).strip()
        if not answer:
            return
        key = turn["key"]
        with self.store.conversation_lock(key):
            record = self.store.read("turns", token)
            # "deferred" turns were buffered by older versions (save interval).
            if record.get("status") not in ("recalled", "deferred"):
                return
            chat = self.store.read("chats", key)
            if chat.get("uncertain"):
                return
            # The server schedules extraction itself, so every answer is saved
            # at once; older buffered answers are sent first to keep turn order.
            pending = [pending_token for pending_token in chat.get("pending", [])
                       if pending_token != token]
            pending.append(token)
            self.store.write("turns", token, {**record, "status": "deferred",
                                              "answer": answer, "occurred_at": time.time()})
            self.store.write("chats", key, {**chat, "pending": pending})
            self._flush_pending(turn)

    def _flush_pending(self, turn):
        """POST /save for one conversation's unsent answers, oldest turn first."""
        key = turn["key"]
        pending = list(self.store.read("chats", key).get("pending", []))
        for token in list(pending):
            record = self.store.read("turns", token)
            if record.get("status") != "deferred":
                pending.remove(token)
                self.store.write("chats", key, {**self.store.read("chats", key), "pending": pending})
                continue
            self.store.write("turns", token, {**record, "status": "save_pending"})
            self.store.write("chats", key, {**self.store.read("chats", key),
                "chat_id": record["chat_id"], "uncertain": True})
            try:
                _request(turn["config"], "POST", "/save",
                         {"chat_id": record["chat_id"], "answer": record["answer"],
                          "occurred_at": record.get("occurred_at")}, timeout=300)
            except Exception as error:  # noqa: BLE001 - report failures at the extension boundary
                self.store.write("turns", token, {**record, "status": "save_failed"})
                self.report(str(error))
                return
            self.store.write("turns", token, {**record, "status": "saved"})
            pending.remove(token)
            current = self.store.read("chats", key)
            if current.get("chat_id") == record["chat_id"]:
                self.store.write("chats", key, {"chat_id": record["chat_id"], "pending": pending})

    def reset_conversation(self, *_args):
        local_id = self.ui_controller.get_current_chat_id()
        def reset():
            config = self.connection()
            key = _digest([config["instance"], config["character"], config["user"], self.chat_identity(local_id)])
            def clear():
                self.store.reset(key)
                with self.state_lock:
                    for token, turn in list(self.turns.items()):
                        if turn["key"] == key:
                            self.store.write("turns", token, {"status": "reset", "key": key})
                            self.turns.pop(token)
                            self.cache.pop(token, None)
                # An interrupted turn may only exist in persisted Newelle history.
                for message in self.controller.get_chat_by_id(local_id):
                    token = message.get(_MARKER)
                    if token and self.store.read("turns", token).get("key") == key:
                        self.store.write("turns", token, {"status": "reset", "key": key})
            self.queue(key).submit(clear).result()
        self.work(reset, lambda result: self.report(_("Memory conversation reset. Learned memories were kept.")))

    def track_graph_tab(self, browser, config):
        """Restyle a live-graph tab when the app theme changes."""
        self._graph_tabs.append((browser, config))
        if self._theme_watch is None:
            def changed(*_args):
                self.refresh_graph_tabs()
            manager = Adw.StyleManager.get_default()
            settings = Gtk.Settings.get_default()
            self._theme_watch = [(manager, manager.connect("notify::dark", changed)),
                                 (settings, settings.connect("notify::gtk-theme-name", changed))]

    def refresh_graph_tabs(self):
        """Re-navigate open graph tabs with the current GTK theme parameters."""
        live = []
        for browser, config in self._graph_tabs:
            if browser.get_parent() is None:  # tab closed
                continue
            params = {"character": config["character"], "user": config["user"]}
            params.update(_graph_embed_params(browser))
            url = (config["url"] + "/gui/embed/knowledge-graph?"
                   + urlencode(params))
            browser.navigate_to(url)
            live.append((browser, config))
        self._graph_tabs[:] = live

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._theme_watch is not None:
            for source, signal in self._theme_watch:
                source.disconnect(signal)
            self._theme_watch = None
        self._graph_tabs.clear()
        executors = list(self.queues.values())
        self.queues.clear()
        for name, original, wrapper in reversed(self.adapters):
            if getattr(self.controller, name, None) is wrapper:
                setattr(self.controller, name, original)
        self.adapters.clear()
        if getattr(self.controller, "_character_memory_adapter", None) is self:
            del self.controller._character_memory_adapter
        if self._shutdown_signal:
            app, signal = self._shutdown_signal
            app.disconnect(signal)
            self._shutdown_signal = None
        # Never block the GTK shutdown/disable callback on subprocess exit.
        def finish():
            for executor in executors:
                executor.shutdown(wait=True)
            self.runtime.stop()
        threading.Thread(target=finish, daemon=True).start()

    def on_uninstall(self):
        self.close()

    def mcp(self):
        loader = getattr(self.controller, "integrationsloader", None)
        integration = loader.get_extension_by_id("mcp") if loader else None
        if integration is None or integration not in loader.get_enabled_extensions():
            raise RuntimeError(_("Enable Newelle’s MCP integration first, then add the tools."))
        return integration

    def toggle_mcp(self, done=None):
        def prepare():
            integration = self.mcp()
            owned_path = self.root / "mcp.json"
            owned = json.loads(owned_path.read_text()) if owned_path.exists() else {}
            if owned:
                return integration, owned, None
            config = self.connection()
            url = config["url"] + "/mcp?" + urlencode({"character": config["character"]})
            if any((s == url if isinstance(s, str) else s.get("url") == url) for s in integration.mcp_servers):
                raise RuntimeError(_("This MCP server is already registered separately. Manage it in Newelle’s MCP settings."))
            prepared = integration.prepare_mcp_server(url=url,
                title="CharacterMemory: " + config["character"], custom_headers={"X-API-Key": config["api_key"]})
            if prepared is None:
                raise RuntimeError(_("Could not discover the CharacterMemory MCP tools."))
            return integration, {"url": url, "signature": _digest(prepared[0])}, prepared
        def commit(result):
            integration, owned, prepared = result
            if self.closed or self.mcp() is not integration:
                raise RuntimeError(_("MCP settings changed while connecting. Try the button again."))
            if prepared is None:
                def remove():
                    registered = next((s for s in integration.mcp_servers
                                       if isinstance(s, dict) and s.get("url") == owned["url"]), None)
                    if registered and _digest(registered) == owned.get("signature"):
                        integration.remove_mcp_server(owned["url"])
                    elif registered:
                        self.report(_("The MCP registration was changed outside this extension; it was left untouched."))
                    self.settings.set_string("mcp-servers", json.dumps(integration.mcp_servers))
                    _atomic_json(self.root / "mcp.json", {})
                remove()
                if done:
                    done(False)
            else:
                if not any(isinstance(s, dict) and s.get("url") == owned["url"] for s in integration.mcp_servers):
                    integration.commit_mcp_server(*prepared)
                else:
                    return False
                self.settings.set_string("mcp-servers", json.dumps(integration.mcp_servers))
                _atomic_json(self.root / "mcp.json", owned)
                if done:
                    done(True)
            return False
        self.work(prepare, commit)

    def open_setup(self, *_args):
        SetupWindow(self).present()


class IndependentWindow(Adw.Window):
    """An independent GTK group escapes grabs from Newelle's modal settings."""
    def __init__(self, **kwargs):
        super().__init__(application=None, transient_for=None, modal=False, **kwargs)
        self._window_group = Gtk.WindowGroup()
        self._window_group.add_window(self)


def _hero(icon_name, title, description):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                  margin_top=12, margin_bottom=12)
    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.set_pixel_size(64)
    icon.add_css_class("accent")
    box.append(icon)
    heading = Gtk.Label(label=title, wrap=True, justify=Gtk.Justification.CENTER)
    heading.add_css_class("title-1")
    box.append(heading)
    subtitle = Gtk.Label(label=description, wrap=True, justify=Gtk.Justification.CENTER,
                         max_width_chars=48)
    subtitle.add_css_class("dim-label")
    box.append(subtitle)
    return box


class SetupWindow(IndependentWindow):
    """Guided setup: server choice → local server → character → ready."""
    def __init__(self, extension):
        super().__init__(title=_("CharacterMemory setup"), default_width=640, default_height=720)
        self.extension = extension
        self.busy = False
        self.discovering = False
        self.step = 0
        self.pages = []
        self.titles = [_("Server"), _("Local server"), _("Character"), _("Ready")]
        self.icons = ["network-server-symbolic", "computer-symbolic",
                      "avatar-default-symbolic", "emblem-ok-symbolic"]
        self.headings = [_("Where should memories live?"), _("Set up the local server"),
                         _("Choose who remembers you"), _("Everything is ready")]
        descriptions = (
            _("Run the memory server on this computer with your Newelle models, or connect to one you already have."),
            _("Install it once. It starts on demand and keeps everything on this computer."),
            _("Pick an existing character, or bring a new one to life."),
            _("Review your setup and turn on long-term memory."),
        )
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        self.step_label = Gtk.Label()
        self.step_label.add_css_class("dim-label")
        header.set_title_widget(self.step_label)
        outer.append(header)
        self.carousel = Adw.Carousel(hexpand=True, vexpand=True)
        # Navigation must pass validation; gestures cannot skip steps.
        self.carousel.set_interactive(False)
        outer.append(self.carousel)
        dots = Adw.CarouselIndicatorDots()
        dots.set_carousel(self.carousel)
        outer.append(dots)
        self.status = Gtk.Label(wrap=True, xalign=0, margin_start=20, margin_end=20,
                                margin_top=12, margin_bottom=4)
        outer.append(self.status)
        self.build_server_page(descriptions[0])
        self.build_local_page(descriptions[1])
        self.build_character_page(descriptions[2])
        self.build_ready_page(descriptions[3])
        footer = Gtk.Box(spacing=12, margin_start=20, margin_end=20,
                         margin_top=12, margin_bottom=20)
        self.back = Gtk.Button(label=_("Back"))
        self.back.add_css_class("pill")
        self.back.connect("clicked", self.go_back)
        footer.append(self.back)
        footer.append(Gtk.Box(hexpand=True))
        self.next = Gtk.Button(label=_("Next"))
        self.next.add_css_class("suggested-action")
        self.next.add_css_class("pill")
        self.next.connect("clicked", self.go_next)
        footer.append(self.next)
        self.done = Gtk.Button(label=_("Done"))
        self.done.add_css_class("suggested-action")
        self.done.add_css_class("pill")
        self.done.connect("clicked", self.finish)
        footer.append(self.done)
        outer.append(footer)
        self.set_content(outer)
        self.local_in_carousel = True
        self.update_mode()
        self.navigate(0, animate=False)

    def build_server_page(self, description):
        page = self.page(description)
        group = Adw.PreferencesGroup()
        self.mode = Adw.ComboRow(title=_("Run memory on"),
            model=Gtk.StringList.new([_("This computer"), _("Another server")]))
        self.mode.set_selected(1 if self.extension.option("instance_mode", "managed") == "custom" else 0)
        group.add(self.mode)
        self.local_hint = Adw.ActionRow(title=_("Your Newelle models, used locally"),
            subtitle=_("The local server starts on demand and uses your selected chat and embedding providers."))
        self.local_hint.add_prefix(Gtk.Image.new_from_icon_name("computer-symbolic"))
        group.add(self.local_hint)
        self.url = Adw.EntryRow(title=_("Server URL"),
            text=self.extension.option("server_url", "http://localhost:8000"))
        self.api_key = Adw.PasswordEntryRow(title=_("API key"),
            text=self.extension.option("api_key", ""))
        group.add(self.url)
        group.add(self.api_key)
        self.test_row = Adw.ActionRow(title=_("Check connection"),
            subtitle=_("Verify the URL and key, and list the characters on the server."))
        self.test_button = self.action_button(_("Connect"), self.test_connection, suggested=True)
        self.test_row.add_suffix(self.test_button)
        group.add(self.test_row)
        page.append(group)
        self.mode.connect("notify::selected", self.update_mode)

    def build_local_page(self, description):
        page = self.page(description)
        group = Adw.PreferencesGroup()
        self.server_status = Adw.ActionRow(title=_("Server status"),
            subtitle=self.extension.runtime.status)
        self.server_status.add_prefix(Gtk.Image.new_from_icon_name("computer-symbolic"))
        group.add(self.server_status)
        controls = Adw.ActionRow(title=_("Server controls"),
            subtitle=_("One click installs the server and starts it."))
        self.install_button = self.action_button("", self.install_and_start, suggested=True)
        self.install_button.add_css_class("pill")
        self.stop_button = self.action_button(_("Stop"), self.stop_server)
        buttons = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        buttons.append(self.install_button)
        buttons.append(self.stop_button)
        controls.add_suffix(buttons)
        group.add(controls)
        page.append(group)
        advanced_group = Adw.PreferencesGroup()
        advanced = Adw.ExpanderRow(title=_("Advanced"), subtitle=_("Installation source and saved data"))
        self.source = Adw.EntryRow(title=_("Local source directory (optional)"),
            text=self.extension.option("install_source", ""))
        advanced.add_row(self.source)
        data_row = Adw.ActionRow(title=_("Data directory"), subtitle=str(self.extension.root))
        advanced.add_row(data_row)
        logs_row = Adw.ActionRow(title=_("Installation and server logs"))
        logs_row.add_suffix(self.action_button(_("View logs"), self.logs))
        advanced.add_row(logs_row)
        advanced_group.add(advanced)
        page.append(advanced_group)
        self.refresh_server_status()

    def build_character_page(self, description):
        page = self.page(description)
        group = Adw.PreferencesGroup(title=_("Your character"),
            description=_("Choose who remembers you and the user ID they know you by."))
        self.user = Adw.EntryRow(title=_("User ID"), text=self.extension.option("user", "user"))
        group.add(self.user)
        self.discovered = Adw.ComboRow(title=_("Available characters"), visible=False)
        self.discovered_handler = self.discovered.connect("notify::selected", self.select_discovered)
        group.add(self.discovered)
        self.character = Adw.EntryRow(title=_("Character"),
            text=self.extension.option("character", ""))
        group.add(self.character)
        new_row = Adw.ActionRow(title=_("Bring a new character to life"),
            subtitle=_("Identity, knowledge, dialogue, and memory — one step at a time."))
        new_row.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        new_row.add_suffix(self.action_button(_("Create character"), self.open_character_wizard, suggested=True))
        group.add(new_row)
        page.append(group)

    def build_ready_page(self, description):
        page = self.page(description)
        self.summary = Gtk.Label(wrap=True, selectable=True, xalign=0)
        review = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=4)
        review.add_css_class("card")
        for side in ("top", "bottom", "start", "end"):
            getattr(self.summary, "set_margin_" + side)(20)
        review.append(self.summary)
        page.append(review)
        self.enable_button = Gtk.Button(label=_("Turn on long-term memory"),
                                        halign=Gtk.Align.CENTER, margin_top=4)
        self.enable_button.add_css_class("suggested-action")
        self.enable_button.add_css_class("pill")
        self.enable_button.connect("clicked", self.enable_memory)
        page.append(self.enable_button)
        self.enabled_label = Gtk.Label(label=_("Long-term memory is already on."), wrap=True,
                                       justify=Gtk.Justification.CENTER, visible=False)
        self.enabled_label.add_css_class("dim-label")
        page.append(self.enabled_label)
        apps = Adw.PreferencesGroup(title=_("Explore"))
        for title, subtitle, icon, graph in (
                (_("Memory browser"), _("Browse and edit what your character remembers."),
                 "system-search-symbolic", False),
                (_("Live knowledge graph"), _("Watch connections light up as you chat."),
                 "view-grid-symbolic", True)):
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            def launch(_button, graph=graph):
                self.apply(announce=False)
                open_memory_app(self.extension, graph)
            row.add_suffix(self.action_button(_("Open"), launch))
            apps.add(row)
        tools_row = Adw.ActionRow(title=_("MCP tools"),
            subtitle=_("Optional. No tools are added automatically."))
        owned = self.extension.root / "mcp.json"
        added = owned.exists() and bool(json.loads(owned.read_text()))
        self.mcp_button = self.action_button(_("Remove tools") if added else _("Add tools"), self.change_mcp)
        tools_row.add_suffix(self.mcp_button)
        apps.add(tools_row)
        page.append(apps)

    def page(self, description):
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                          margin_top=8, margin_bottom=24)
        content.append(_hero(self.icons[len(self.pages)], self.headings[len(self.pages)], description))
        clamp = Adw.Clamp(maximum_size=480, tightening_threshold=360,
                          margin_start=28, margin_end=28)
        clamp.set_child(content)
        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(clamp)
        self.pages.append(scroll)
        self.carousel.append(scroll)
        return content

    @staticmethod
    def action_button(label, callback, suggested=False):
        button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        if suggested:
            button.add_css_class("suggested-action")
        button.connect("clicked", callback)
        return button

    def managed(self):
        return self.mode.get_selected() == 0

    def next_index(self):
        if self.step == 0:
            return 1 if self.managed() else 2
        return self.step + 1

    def prev_index(self):
        if self.step == 2 and not self.managed():
            return 0
        return self.step - 1

    def navigate(self, step, animate=True):
        self.step = step
        self.carousel.scroll_to(self.pages[step], animate)
        total = 4 if self.managed() else 3
        number = step + 1 if self.managed() or step == 0 else step
        self.step_label.set_text(_("Step %(number)d of %(total)d — %(title)s") %
                                 {"number": number, "total": total, "title": self.titles[step]})
        if step == 1:
            self.refresh_server_status()
        elif step == 2:
            self.discover()
        elif step == 3:
            self.render_summary()
        self.update_navigation()

    def update_navigation(self):
        self.back.set_sensitive(self.step > 0 and not self.busy)
        self.next.set_visible(self.step < 3)
        self.next.set_sensitive(not self.busy
                                and (self.step != 1 or self.extension.runtime.is_running))
        self.done.set_visible(self.step == 3)

    def go_next(self, *_args):
        if self.busy or self.step >= 3:
            return
        if self.step == 0:
            self.apply(announce=False)
        elif self.step == 1:
            if not self.extension.runtime.is_running:
                self.status.set_text(_("Start the local server before continuing."))
                return
        elif self.step == 2:
            if not self.character.get_text().strip():
                self.status.set_text(_("Choose or create a character before continuing."))
                return
            self.apply(announce=False)
        self.status.set_text("")
        self.navigate(self.next_index())

    def go_back(self, *_args):
        if self.busy or self.step == 0:
            return
        self.status.set_text("")
        self.navigate(self.prev_index())

    def finish(self, *_args):
        self.apply(announce=False)
        self.close()

    def update_mode(self, *_args):
        custom = self.mode.get_selected() == 1
        self.url.set_visible(custom)
        self.api_key.set_visible(custom)
        self.test_row.set_visible(custom)
        self.local_hint.set_visible(not custom)
        # External server: drop the local-server step from the carousel
        # entirely, so neither the dots nor the page transition reveal it.
        if custom and self.local_in_carousel:
            self.carousel.remove(self.pages[1])
            self.local_in_carousel = False
        elif not custom and not self.local_in_carousel:
            self.carousel.insert(self.pages[1], 1)
            self.local_in_carousel = True
        mode = "custom" if custom else "managed"
        if self.extension.option("instance_mode", "managed") != mode:
            self.extension.set_setting("instance_mode", mode)
            self.extension.settings_update()
        self.update_navigation()

    def apply(self, *_args, announce=True):
        for key, value in (("instance_mode", "custom" if self.mode.get_selected() else "managed"),
                           ("server_url", self.url.get_text()), ("api_key", self.api_key.get_text()),
                           ("user", self.user.get_text()), ("character", self.character.get_text().strip()),
                           ("install_source", self.source.get_text())):
            self.extension.set_setting(key, value)
        self.extension.settings_update()
        if announce:
            self.status.set_text(_("Configuration saved."))

    def test_connection(self, *_args):
        if self.busy:
            return
        self.apply(announce=False)
        self.busy = True
        self.test_button.set_sensitive(False)
        self.update_navigation()
        self.status.set_text(_("Connecting…"))
        def fetch():
            try:
                count = len(_request(self.extension.connection(False), "GET", "/")["characters"])
                GLib.idle_add(self.connected, count, None)
            except Exception as error:  # noqa: BLE001 - display connection errors in the window
                GLib.idle_add(self.connected, 0, str(error))
        self.extension.work(fetch)

    def connected(self, count, error):
        self.busy = False
        self.test_button.set_sensitive(True)
        self.update_navigation()
        self.status.set_text(error or _("Connected. Found %(count)d character(s).") % {"count": count})
        return False

    def refresh_server_status(self):
        runtime = self.extension.runtime
        self.server_status.set_subtitle(runtime.status)
        self.install_button.set_visible(not runtime.is_running)
        self.install_button.set_label(_("Start") if runtime.installed else _("Install and start"))
        self.stop_button.set_visible(runtime.is_running)

    def install_and_start(self, *_args):
        if self.busy:
            return
        self.apply(announce=False)
        self.busy = True
        self.install_button.set_sensitive(False)
        self.update_navigation()
        def poll():
            if not self.busy:
                return False
            self.refresh_server_status()
            self.status.set_text(self.extension.runtime.status)
            return True
        GLib.timeout_add(500, poll)
        def task():
            error = None
            try:
                if not self.extension.runtime.installed:
                    self.extension.runtime.install()
                self.extension.runtime.start()
            except Exception as failure:  # noqa: BLE001 - surfaced in the setup status
                error = str(failure)
            GLib.idle_add(self.install_finished, error)
        self.extension.work(task)

    def install_finished(self, error):
        self.busy = False
        self.install_button.set_sensitive(True)
        self.refresh_server_status()
        self.update_navigation()
        if error:
            self.status.set_text(error)
        elif self.extension.runtime.is_running:
            self.status.set_text(_("The local server is running. Continue to choose a character."))
            # The extension settings widen from the lone setup button to the full list.
            self.extension.settings_update()
        return False

    def stop_server(self, *_args):
        if self.busy:
            return
        self.busy = True
        self.update_navigation()
        def task():
            self.extension.runtime.stop()
            GLib.idle_add(self.server_stopped)
        self.extension.work(task)

    def server_stopped(self):
        self.busy = False
        self.refresh_server_status()
        self.update_navigation()
        self.status.set_text(_("The local server was stopped."))
        return False

    def discover(self):
        if self.discovering:
            return
        self.discovering = True
        self.apply(announce=False)
        self.status.set_text(_("Looking for characters…"))
        def fetch():
            try:
                names = _request(self.extension.connection(False), "GET", "/")["characters"]
                GLib.idle_add(self.show_characters, names, None)
            except Exception as error:  # noqa: BLE001 - display connection errors in the window
                GLib.idle_add(self.show_characters, [], str(error))
        self.extension.work(fetch)

    def show_characters(self, names, error):
        self.discovering = False
        if error:
            self.status.set_text(error)
            return False
        current = self.character.get_text().strip()
        # Swapping the model resets the selection; keep typed names intact.
        self.discovered.handler_block(self.discovered_handler)
        self.discovered.set_model(Gtk.StringList.new(names))
        self.discovered.set_visible(bool(names))
        if current in names:
            self.discovered.set_selected(names.index(current))
        self.discovered.handler_unblock(self.discovered_handler)
        if not names:
            self.status.set_text(_("No characters yet on this server. Create your first character."))
        elif current in names:
            self.status.set_text(_("Connected. Choose a character below, or create a new one."))
        elif current:
            self.status.set_text(_("“%(name)s” is not on this server. Choose one below or create it.")
                                 % {"name": current})
        else:
            self.character.set_text(names[0])
            self.status.set_text(_("Connected. Choose a character below, or create a new one."))
        return False

    def select_discovered(self, *_args):
        item = self.discovered.get_selected_item()
        if item is not None:
            self.character.set_text(item.get_string())

    def render_summary(self):
        where = _("This computer") if self.managed() else self.url.get_text().strip()
        self.summary.set_text("\n".join([
            _("Server: ") + where,
            _("Character: ") + (self.character.get_text().strip() or _("None")),
            _("User ID: ") + self.user.get_text().strip(),
        ]))
        settings = self.extension.settings
        active = (settings.get_string("memory-model") == CharacterMemoryHandler.key
                  and settings.get_boolean("memory-on"))
        self.enable_button.set_visible(not active)
        self.enabled_label.set_visible(active)

    def enable_memory(self, *_args):
        self.apply(announce=False)
        settings = self.extension.settings
        settings.set_string("memory-model", CharacterMemoryHandler.key)
        settings.set_boolean("memory-on", True)
        self.enable_button.set_visible(False)
        self.enabled_label.set_visible(True)
        self.status.set_text(_("Long-term memory is on. Your character will remember your chats."))

    def open_character_wizard(self, *_args):
        self.apply(announce=False)
        CharacterWizard(self.extension, on_created=self.character_created).present()

    def character_created(self, name):
        self.character.set_text(name)
        self.status.set_text(_("“%(name)s” is ready. Continue when you like.") % {"name": name})

    def change_mcp(self, *_args):
        self.apply(announce=False)
        self.extension.toggle_mcp(self.mcp_done)

    def mcp_done(self, added):
        self.mcp_button.set_label(_("Remove tools") if added else _("Add tools"))
        return False

    def logs(self, *_args):
        window = IndependentWindow(title=_("Managed instance logs"), default_width=700, default_height=450)
        text = []
        for name in ("install.log", "server.log"):
            path = self.extension.runtime.root / name
            if path.exists():
                text.append(name + "\n" + path.read_text(errors="replace")[-30000:])
        view = Gtk.TextView(editable=False, monospace=True)
        view.get_buffer().set_text("\n\n".join(text) or _("No logs yet."))
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(view)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(Adw.HeaderBar())
        scroll.set_vexpand(True)
        content.append(scroll)
        window.set_content(content)
        window.present()


def create_character(extension, config, draft, progress):
    """Resumable admin workflow. Repeated PUTs are safe; POSTs are journalled."""
    path = extension.root / "character-draft.json"
    identity = _digest([config["instance"], draft["name"]])
    if draft.get("created_for") != identity:
        names = _request(config, "GET", "/")["characters"]
        if draft["name"] in names:
            if draft.get("attempted_for") != identity:
                raise RuntimeError(_("That character already exists. Choose another name; existing characters will not be overwritten."))
        else:
            draft["attempted_for"] = identity
            _atomic_json(path, draft)
            try:
                _request(config, "POST", "/api/admin/characters", {"name": draft["name"]})
            except MemoryHTTPError as error:
                if 400 <= error.status < 500:
                    draft.pop("attempted_for", None)
                    _atomic_json(path, draft)
                raise
        draft["created_for"] = identity
        draft.pop("job_id", None)
        _atomic_json(path, draft)
    name = quote(draft["name"], safe="")
    base = "/api/admin/characters/" + name
    if not draft.get("job_id"):
        progress(_("Saving persona and source files…"))
        memory = {"enabled_" + k: v == 1 for k, v in draft["choices"].items()
                  if v and k != "knowledge_graph"}
        patch = {"persona": draft["persona"], "memory": memory}
        if draft["choices"].get("knowledge_graph"):
            patch["kg_enabled"] = draft["choices"]["knowledge_graph"] == 1
        _request(config, "PUT", base + "/config", patch)
        for key, bucket, filename in (("knowledge", "information", "newelle.md"),
                                      ("dialogues", "dialogues", "newelle.txt")):
            # Empty PUT also clears text removed from a resumed draft.
            _request(config, "PUT", base + "/files/" + bucket + "/" + filename,
                     {"content": draft[key]})
        draft["job_id"] = _request(config, "POST", base + "/rebuild")["job_id"]
        _atomic_json(path, draft)
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        try:
            job = _request(config, "GET", "/api/jobs/" + quote(draft["job_id"], safe=""))
        except MemoryHTTPError as error:
            if error.status == 404:
                draft.pop("job_id", None)
                _atomic_json(path, draft)
                raise RuntimeError(_("The rebuild job expired or the server restarted. Resume creation to rebuild again.")) from error
            raise
        progress(str(job.get("stage", "")) + " " + str(job.get("detail", "")))
        if job["state"] == "done":
            return
        if job["state"] == "error":
            draft.pop("job_id", None)
            _atomic_json(path, draft)
            raise RuntimeError(_("Character rebuild failed. Review the server logs and resume creation."))
        time.sleep(1)
    raise RuntimeError(_("Rebuild is still running. Resume creation to check progress."))


def read_character_files(files):
    """Read selected local/portal files without changing the editor on failure."""
    texts = []
    for file in files:
        name = file.get_basename() or _("Selected file")
        if Path(name).suffix.lower() not in (".md", ".txt"):
            raise ValueError(_("%(name)s: choose a Markdown (.md) or plain-text (.txt) file.") % {"name": name})
        stream = file.read(None)
        try:
            limit = 5 * 1024 * 1024
            chunks, size = [], 0
            while size <= limit:
                chunk = stream.read_bytes(min(65536, limit + 1 - size), None).get_data()
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            data = b"".join(chunks)
        finally:
            stream.close(None)
        if len(data) > 5 * 1024 * 1024:
            raise ValueError(_("%(name)s is larger than 5 MiB. Split it into smaller text files.") % {"name": name})
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError(_("%(name)s is not UTF-8 text. Save it as UTF-8 and try again.") % {"name": name}) from error
        if "\x00" in text:
            raise ValueError(_("%(name)s contains binary data. Choose a text file.") % {"name": name})
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if text:
            texts.append(text)
    if not texts:
        raise ValueError(_("The selected files contain no text."))
    return "\n\n".join(texts)


class CharacterWizard(IndependentWindow):
    """Identity → knowledge → dialogues → memories → reviewed build."""
    def __init__(self, extension, on_created=None):
        super().__init__(title=_("Create a character"), default_width=640, default_height=720)
        self.extension = extension
        self.on_created = on_created
        self.draft_path = extension.root / "character-draft.json"
        self.draft = json.loads(self.draft_path.read_text()) if self.draft_path.exists() else {}
        self.step = 0
        self.busy = False
        self.finished = False
        self.inputs = {}
        self.toggles = {}
        self.pages = []
        self.titles = [_("Identity"), _("Knowledge"), _("Dialogues"), _("Memories"), _("Build")]
        self.icons = ["avatar-default-symbolic", "accessories-dictionary-symbolic",
                      "user-available-symbolic", "document-open-recent-symbolic", "object-select-symbolic"]
        self.headings = [_("Who is your character?"), _("Give them a world"),
                         _("Let their voice come through"), _("Choose what stays with them"),
                         _("Ready to bring them to life")]
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        self.step_label = Gtk.Label()
        self.step_label.add_css_class("dim-label")
        header.set_title_widget(self.step_label)
        outer.append(header)
        self.carousel = Adw.Carousel(hexpand=True, vexpand=True)
        # Navigation must pass validation; gestures cannot skip the identity step.
        self.carousel.set_interactive(False)
        outer.append(self.carousel)
        dots = Adw.CarouselIndicatorDots()
        dots.set_carousel(self.carousel)
        outer.append(dots)
        self.set_content(outer)

        identity = self.page(_("Pick a name and describe who the character is."))
        self.field(identity, "name", _("Name"))
        self.field(identity, "persona", _("Persona (optional)"))
        knowledge = self.page(_("Upload lore or reference files, or write directly below. This step is optional."))
        self.field(knowledge, "knowledge", _("Initial knowledge"))
        dialogues = self.page(_("Upload example conversations or write your own to shape the character’s voice. This step is optional."))
        self.field(dialogues, "dialogues", _("Dialogue examples"))
        memories = self.page(_("Choose which memories the character should maintain. The knowledge graph is enabled by default and may take longer to build; leave the rest on Default to keep the server’s setting."))
        names = (("character_info", _("Character information")), ("character_self", _("Character self")),
                 ("prospective", _("Intentions")), ("dialogue_style", _("Dialogue style")),
                 ("user_facts", _("User facts")), ("user_directives", _("User directives")),
                 ("episodic", _("Episodes")), ("conversation_events", _("Conversation events")),
                 ("emotion", _("Emotion")), ("world", _("World")), ("calendar", _("Calendar")),
                 ("heartbeat", _("Heartbeat")), ("user_summary", _("User summary")),
                 ("groups", _("Groups")), ("knowledge_graph", _("Knowledge graph")))
        self.memory_titles = dict(names)
        memory_group = Adw.PreferencesGroup()
        memories.append(memory_group)
        for name, title in names:
            choice = Adw.ComboRow(title=title,
                model=Gtk.StringList.new([_("Default"), _("On"), _("Off")]))
            choice.set_selected(self.draft.get("choices", {}).get(
                name, 1 if name == "knowledge_graph" else 0))
            memory_group.add(choice)
            self.toggles[name] = choice
        build = self.page(_("Review your character, then build its indexes. This saves the persona, indexes the knowledge and dialogues, and builds the knowledge graph if enabled."))
        self.summary = Gtk.Label(wrap=True, selectable=True, xalign=0)
        review = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=4)
        review.add_css_class("card")
        for side in ("top", "bottom", "start", "end"):
            getattr(self.summary, "set_margin_" + side)(20)
        review.append(self.summary)
        build.append(review)
        self.status = Gtk.Label(wrap=True, xalign=0, margin_start=20, margin_end=20,
                                margin_top=12, margin_bottom=12)
        outer.append(self.status)
        footer = Gtk.Box(spacing=12, margin_start=20, margin_end=20,
                         margin_top=12, margin_bottom=20)
        self.back = Gtk.Button(label=_("Back"))
        self.back.add_css_class("pill")
        self.back.connect("clicked", self.go_back)
        footer.append(self.back)
        footer.append(Gtk.Box(hexpand=True))
        self.skip = Gtk.Button(label=_("Skip"))
        self.skip.add_css_class("flat")
        self.skip.set_tooltip_text(_("Continue with the current values or defaults."))
        self.skip.connect("clicked", self.skip_step)
        footer.append(self.skip)
        self.next = Gtk.Button(label=_("Next"))
        self.next.add_css_class("suggested-action")
        self.next.add_css_class("pill")
        self.next.connect("clicked", self.go_next)
        footer.append(self.next)
        self.create = Gtk.Button(label=_("Build character"))
        self.create.add_css_class("suggested-action")
        self.create.add_css_class("pill")
        self.create.connect("clicked", self.submit)
        footer.append(self.create)
        outer.append(footer)
        if self.draft.get("job_id"):
            self.render_summary()
            self.navigate(4, animate=False)
            self.status.set_text(_("Resume to check the previous rebuild’s progress."))
        else:
            self.navigate(0, animate=False)

    def page(self, description):
        index = len(self.pages)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                          margin_top=8, margin_bottom=24)
        content.append(_hero(self.icons[index], self.headings[index], description))
        clamp = Adw.Clamp(maximum_size=480, tightening_threshold=360,
                          margin_start=28, margin_end=28)
        clamp.set_child(content)
        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(clamp)
        self.pages.append(scroll)
        self.carousel.append(scroll)
        return content

    def field(self, page, key, title):
        if key == "name":
            group = Adw.PreferencesGroup()
            widget = Adw.EntryRow(title=title, text=self.draft.get(key, ""))
            widget.connect("entry-activated", self.go_next)
            group.add(widget)
            page.append(group)
        else:
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            heading = Gtk.Box(spacing=12)
            label = Gtk.Label(label=title, xalign=0)
            label.add_css_class("heading")
            label.set_hexpand(True)
            heading.append(label)
            if key in ("knowledge", "dialogues"):
                upload = Gtk.Button(label=_("Upload files…"))
                upload.connect("clicked", self.choose_files, key)
                heading.append(upload)
            group.append(heading)
            if key in ("knowledge", "dialogues"):
                hint = Gtk.Label(label=_("UTF-8 text · .md or .txt · up to 5 MiB per file"),
                                 wrap=True, xalign=0)
                hint.add_css_class("dim-label")
                group.append(hint)
                self.format_help(group, key)
            widget = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR,
                top_margin=14, bottom_margin=14, left_margin=16, right_margin=16)
            widget.get_buffer().set_text(self.draft.get(key, ""))
            scroll = Gtk.ScrolledWindow(min_content_height=140 if key == "persona" else 220)
            scroll.add_css_class("card")
            scroll.add_css_class("frame")
            scroll.set_child(widget)
            group.append(scroll)
            page.append(group)
        self.inputs[key] = widget

    @staticmethod
    def format_help(group, key):
        knowledge = key == "knowledge"
        formats = Adw.PreferencesGroup()
        row = Adw.ExpanderRow(title=_("Expected format"), subtitle=(
            _("Markdown headings and paragraphs, or plain prose.") if knowledge else
            _("Speaker: text, with a blank line between turns.")))
        example = (_("# Background\nKurisu is a scientist.\n\n## Interests\nShe enjoys experiments and puzzles.") if knowledge else
                   _("User: What are you working on?\n\nKurisu: An experiment. Try not to touch anything.\n\nUser: Can I help?"))
        label = Gtk.Label(label=example, xalign=0, selectable=True, wrap=True,
                          margin_top=12, margin_bottom=12, margin_start=16, margin_end=16)
        label.add_css_class("monospace")
        row.add_row(label)
        formats.add(row)
        group.append(formats)

    def choose_files(self, _button, key):
        if self.busy or self.finished or self.draft.get("job_id"):
            return
        filter_ = Gtk.FileFilter(name=_("Markdown and text files"),
                                patterns=["*.md", "*.txt", "*.MD", "*.TXT"])
        dialog = Gtk.FileDialog(title=_("Upload knowledge files") if key == "knowledge" else
                                _("Upload dialogue examples"), modal=True, default_filter=filter_)
        dialog.open_multiple(self, None, self.files_selected, key)

    def files_selected(self, dialog, result, key):
        try:
            selected = dialog.open_multiple_finish(result)
        except GLib.Error as error:
            if not (error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED)
                    or error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.CANCELLED)
                    or error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)):
                self.status.set_text(_("Could not open the file chooser: ") + str(error))
            return
        files = [selected.get_item(i) for i in range(selected.get_n_items())]
        if not files:
            return
        self.busy = True
        self.update_navigation()
        self.status.set_text(_("Reading files…"))
        def read():
            try:
                text = read_character_files(files)
                GLib.idle_add(self.files_loaded, key, text, len(files), None)
            except Exception as error:  # noqa: BLE001 - preserve editor and report file/portal errors
                GLib.idle_add(self.files_loaded, key, None, 0, str(error))
        self.extension.work(read)

    def files_loaded(self, key, text, count, error):
        self.busy = False
        self.update_navigation()
        if error:
            self.status.set_text(error)
            return False
        buffer = self.inputs[key].get_buffer()
        existing = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).rstrip()
        buffer.set_text(existing + ("\n\n" if existing else "") + text)
        if self.save_draft():
            self.status.set_text(_("Added %(count)d file(s). You can edit the imported text below.") % {"count": count})
        return False

    def save_draft(self):
        values = {"name": self.inputs["name"].get_text().strip()}
        for key in ("persona", "knowledge", "dialogues"):
            buffer = self.inputs[key].get_buffer()
            values[key] = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()
        name = values["name"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,62}", name) or ".." in name:
            self.status.set_text(_("Use a name starting with a letter or digit, up to 63 characters; letters, digits, spaces, dots, dashes, and underscores only."))
            return False
        self.draft.update(values)
        self.draft["choices"] = {name: widget.get_selected() for name, widget in self.toggles.items()}
        _atomic_json(self.draft_path, self.draft)
        return True

    def render_summary(self):
        choices = [f"{self.memory_titles[name]}: {(_('On') if value == 1 else _('Off'))}"
                   for name, value in self.draft.get("choices", {}).items() if value and name in self.memory_titles]
        self.summary.set_text("\n\n".join([
            _("Name: ") + self.draft.get("name", ""),
            _("Persona: ") + (self.draft.get("persona") or _("Not provided")),
            _("Knowledge: %(count)d characters") % {"count": len(self.draft.get("knowledge", ""))},
            _("Dialogue examples: %(count)d characters") % {"count": len(self.draft.get("dialogues", ""))},
            _("Memory changes: ") + (", ".join(choices) if choices else _("Server defaults")),
        ]))

    def navigate(self, step, animate=True):
        self.step = step
        self.carousel.scroll_to(self.pages[step], animate)
        self.step_label.set_text(_("Step %(step)d of 5 — %(title)s") %
                                 {"step": step + 1, "title": self.titles[step]})
        self.update_navigation()

    def update_navigation(self):
        locked = self.busy or self.finished or bool(self.draft.get("job_id"))
        self.back.set_sensitive(self.step > 0 and not locked)
        self.skip.set_visible(1 <= self.step <= 3)
        self.skip.set_sensitive(not locked)
        self.next.set_visible(self.step < 4)
        self.next.set_sensitive(not locked)
        self.create.set_visible(self.step == 4)
        self.create.set_sensitive(self.step == 4 and not self.busy and not self.finished)
        self.create.set_label(_("Resume build") if self.draft.get("created_for") or self.draft.get("job_id") else _("Build character"))

    def skip_step(self, *_args):
        if 1 <= self.step <= 3:
            self.go_next()

    def go_next(self, *_args):
        if self.busy or self.finished or self.step >= 4 or self.draft.get("job_id"):
            return
        if self.save_draft():
            self.status.set_text("")
            if self.step == 3:
                self.render_summary()
            self.navigate(self.step + 1)

    def go_back(self, *_args):
        if self.busy or self.finished or self.step == 0 or self.draft.get("job_id"):
            return
        if self.save_draft():
            self.status.set_text("")
            self.navigate(self.step - 1)

    def submit(self, *_args):
        if self.step != 4 or self.busy or self.finished:
            return
        if not self.draft.get("job_id") and not self.save_draft():
            self.navigate(0)
            return
        self.busy = True
        self.update_navigation()
        self.status.set_text(_("Building…"))
        draft = json.loads(json.dumps(self.draft))
        def task():
            try:
                config = self.extension.connection(False)
                create_character(self.extension, config, draft,
                    lambda text: GLib.idle_add(self.status.set_text, text))
                GLib.idle_add(self.completed, draft["name"])
            except Exception as error:  # noqa: BLE001 - report failures at the extension boundary
                _atomic_json(self.draft_path, draft)
                GLib.idle_add(self.failed, str(error), draft)
        self.extension.work(task)

    def failed(self, message, draft):
        self.draft = draft
        self.busy = False
        self.status.set_text(message)
        self.update_navigation()
        return False

    def completed(self, name):
        self.extension.set_setting("character", name)
        self.extension.settings_update()
        self.draft_path.unlink(missing_ok=True)
        self.busy = False
        self.finished = True
        self.status.set_text(_("Character created and selected."))
        self.update_navigation()
        if self.on_created is not None:
            self.on_created(name)
        return False
