import threading
import requests
import os
import subprocess
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pydub import AudioSegment
from time import sleep
from urllib.parse import urlencode, urljoin
from gi.repository import Gtk
from livepng import LivePNG
from .utility.system import get_spawn_command

from .handlers.avatar import AvatarHandler
from .handlers.tts import TTSHandler
from .extensions import NewelleExtension

class NyarchDesktopPuppet(NewelleExtension):
    name = "Nyarch Desktop Puppet"
    id="puppet"

    def get_avatar_handlers(self) -> list[dict]:
        return [{
            "key": "puppet",
            "title": "Nyarch Desktop Puppet",
            "description": "Get acchan on your desktop!",
            "class": Live2DPuppetAvatarHandler,
        }]

class Live2DPuppetAvatarHandler(AvatarHandler):
    key = "puppet"
    base_url = "http://127.0.0.1:42943"
    lockfile = None
    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self._wait_js = threading.Event()
        self._expressions_raw = []
        self.webview_path = os.path.join(path, "avatars", "live2d", "web")
        self.puppet_path = os.path.join(path, "avatars", "live2d", "DesktopPuppet")
        self.models_dir = os.path.join(self.webview_path, "models")
        self._puppet_process = None

    def get_available_models(self):
        file_list = []
        for root, _, files in os.walk(self.models_dir):
            for file in files:
                if file.endswith('.model3.json') or file.endswith('.model.json'):
                    file_name = file.rstrip('.model3.json').rstrip('.model.json')
                    relative_path = os.path.relpath(os.path.join(root, file), self.models_dir)
                    file_list.append((file_name, relative_path))
        return file_list

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "model",
                "title": _("Live2D Model"),
                "description": _("Live2D Model to use"),
                "type": "combo",
                "values": self.get_available_models(),
                "default": "Arch/arch chan model0.model3.json",
                "folder": os.path.abspath(self.models_dir),
                "refresh": lambda x: self.settings_update(),
            },
            {
                "key": "update_model",
                "title": "Update Puppet Program",
                "description": "Update Puppet program",
                "type": "button",
                "default": "",
                "label": "Update",
                "callback": self.update_puppet,

            },
            {
                "key": "start_window_server",
                "title": "Automatically run puppet",
                "description": "Automatically run puppet window server",
                "type": "toggle",
                "default": True
            },
            {
             "key": "fps",
                "title": _("Lipsync Framerate"),
                "description": _("Maximum amount of frames to generate for lipsync"),
                "type": "range",
                "min": 5,
                "max": 30,
                "default": 10.0,
                "round-digits": 0
            },
            {
                "key": "scale",
                "title": _("Zoom Model"),
                "description": _("Zoom the Live2D model"),
                "type": "range",
                "min": 5,
                "max": 300,
                "default": 100,
                "round-digits": 0
            },
            {
                "key": "extra_w",
                "title": _("Extra Scaling Width"),
                "description": _("Extra scaling width for the input. Make it smaller than one if the input area takes too much width"),
                "type": "range",
                "min": 0,
                "max": 2,
                "default": 0.56,
                "round-digits": 2
            },
            {
                "key": "extra_h",
                "title": _("Extra Scaling Height"),
                "description": _("Extra scaling height for the input. Make it smaller than one if the input area takes too much height"),
                "type": "range",
                "min": 0,
                "max": 2,
                "default": 1,
                "round-digits": 2
            },
            {
                "key": "automatic_hide",
                "title": "Automatically hide/show the model",
                "description": "Automatically bring the model in overlay or background when it is talking",
                "type": "toggle",
                "default": False
            },
            {
                "key": "start_overlay",
                "title": "Start overlay type",
                "description": "Choose if start the model in overlay or in background",
                "type": "combo",
                "values": (("overlay", "overlay"), ("background", "background")),
                "default": "overlay"
            }
        ]
    def is_installed(self) -> bool:
        return os.path.isdir(self.webview_path) and os.path.isdir(self.puppet_path)

    def install(self):
        if not os.path.isdir(self.webview_path):
            subprocess.check_output(["git", "clone", "https://github.com/NyarchLinux/live2d-lipsync-viewer.git", self.webview_path])
            subprocess.check_output(["wget", "-P", os.path.join(self.models_dir), "http://mirror.nyarchlinux.moe/Arch.tar.xz"])
            subprocess.check_output(["tar", "-Jxf", os.path.join(self.models_dir, "Arch.tar.xz"), "-C", self.models_dir])
            subprocess.Popen(["rm", os.path.join(self.models_dir, "Arch.tar.xz")])
        elif not os.path.isdir(self.puppet_path):
            subprocess.check_output(["git", "clone", "https://github.com/NyarchLinux/DesktopPuppet", self.puppet_path])

    def update_puppet(self, button):
        subprocess.check_output(["bash", "-c", "cd " + self.puppet_path + " && git pull"])
        self.settings_update()

    def start_desktop_puppet_process(self):
        if not self.get_setting("start_window_server"):
            return
        self.lockfile = os.path.join(self.puppet_path, "src", "nyarchlinux-desktop-puppet.lock")
        self._puppet_process = subprocess.Popen(get_spawn_command() + ["python3", os.path.join(self.puppet_path,"src", "main.py")])

    def __start_webserver(self):
        folder_path = self.webview_path
        class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
            def translate_path(self, path):
                # Get the default translate path
                path = super().translate_path(path)
                # Replace the default directory with the specified folder path
                return os.path.join(folder_path, os.path.relpath(path, os.getcwd()))
        self.httpd = HTTPServer(('localhost', 0), CustomHTTPRequestHandler)
        httpd = self.httpd
        model = self.get_setting("model")
        background_color = self.get_setting("background-color")
        scale = int(self.get_setting("scale"))/100
        q = urlencode({"model": model, "bg": background_color, "scale": scale})

        self.model_webserver_address = "http://localhost:" + str(httpd.server_address[1])
        threading.Thread(target=self.update_address).start()
        httpd.serve_forever()

    def update_address(self):
        try:
            sleep(2)
            settings = {"address": self.model_webserver_address, "model": self.get_setting("model"), "scale": self.get_setting("scale"), "extra_w": self.get_setting("extra_w"), "extra_h": self.get_setting("extra_h")}
            requests.post(f'{self.base_url}/set_settings', json={'settings': settings})
            sleep(2)
            self.set_overlay(self.get_setting("start_overlay"))
        except Exception as e:
            print(e)
            return

    def create_gtk_widget(self) -> Gtk.Widget:
        threading.Thread(target=self.__start_webserver).start()
        threading.Thread(target=self.start_desktop_puppet_process).start()
        return Gtk.Box()

    def destroy(self, add=None):
        self.httpd.shutdown()
        if self.lockfile is not None and os.path.isfile(self.lockfile):
            os.remove(self.lockfile)

    def get_expressions(self):
        if len(self._expressions_raw) > 0:
            return self._expressions_raw
        self._expressions_raw = []
        response = requests.get(f'{self.base_url}/expressions')
        if response.status_code == 200:
            self._expressions_raw = response.json()
        else:
            return []
        return self._expressions_raw

    def set_expression(self, expression : str):
        requests.post(f'{self.base_url}/expression', json={'expression': expression})

    def set_overlay(self, overlay : str):
        requests.post(f'{self.base_url}/overlay', json={'overlay': overlay})

    def speak(self, path: str, tts: TTSHandler, frame_rate: int):
        tts.stop()
        if self.get_setting("automatic_hide"):
            self.set_overlay("overlay")
        audio = AudioSegment.from_file(path)
        sample_rate = audio.frame_rate
        audio_data = audio.get_array_of_samples()
        amplitudes = LivePNG.calculate_amplitudes(sample_rate, audio_data, frame_rate=frame_rate)
        t1 = threading.Thread(target=self._start_animation, args=(amplitudes, frame_rate))
        t2 = threading.Thread(target=tts.playsound, args=(path, ))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        if self.get_setting("automatic_hide"):
            self.set_overlay("background")

    def _start_animation(self, amplitudes: list[float], frame_rate=10):
        max_amplitude = max(amplitudes)
        for amplitude in amplitudes:
            if self.stop_request:
                self.set_mouth(0)
                return
            self.set_mouth(amplitude/max_amplitude)
            sleep(1/frame_rate)

    def set_mouth(self, value):
        requests.post(f'{self.base_url}/mouth', json={'amplitude': value})

    def set_model_path(self, path: str):
        """Sends a model path to the server."""
        response = requests.post(f'{self.base_url}/model_path', json={'path': path})
        response.raise_for_status()
