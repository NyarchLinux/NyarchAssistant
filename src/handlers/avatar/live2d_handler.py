import threading
import os
import json
import subprocess
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pydub import AudioSegment
from time import sleep
from urllib.parse import urlencode, urljoin
from gi.repository import Gtk, WebKit, GLib
from livepng import LivePNG

from ...utility.strings import rgb_to_hex
from .avatar import AvatarHandler
from ..tts import TTSHandler

class Live2DHandler(AvatarHandler):
    key = "Live2D"
    _wait_js : threading.Event
    _expressions_raw : list[str]
    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self._expressions_raw = []
        self._wait_js = threading.Event()
        self.webview_path = os.path.join(path, "avatars", "live2d", "web")
        self.models_dir = os.path.join(self.webview_path, "models")

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
        widget = Gtk.Box()
        color = widget.get_style_context().lookup_color('window_bg_color')[1]
        default = rgb_to_hex(color.red, color.green, color.blue)

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
                "key": "background-color",
                "title": _("Background Color"),
                "description": _("Background color of the avatar"),
                "type": "entry",
                "default": default,
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
            }
        ]
    def is_installed(self) -> bool:
        return os.path.isdir(self.webview_path)

    def install(self):
        subprocess.check_output(["git", "clone", "https://github.com/NyarchLinux/live2d-lipsync-viewer.git", self.webview_path])
        subprocess.check_output(["wget", "-P", os.path.join(self.models_dir), "http://mirror.nyarchlinux.moe/Arch.tar.xz"])
        subprocess.check_output(["tar", "-Jxf", os.path.join(self.models_dir, "Arch.tar.xz"), "-C", self.models_dir])
        subprocess.Popen(["rm", os.path.join(self.models_dir, "Arch.tar.xz")])
    
    def __start_webserver(self):
        folder_path = self.webview_path
        class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
            def translate_path(self, path):
                # Get the default translate path
                path = super().translate_path(path)
                # Replace the default directory with the specified folder path
                return os.path.join(folder_path, os.path.relpath(path, os.getcwd()))
        self.httpd = HTTPServer(('127.0.0.1', 0), CustomHTTPRequestHandler)
        httpd = self.httpd
        model = self.get_setting("model")
        background_color = self.get_setting("background-color")
        scale = int(self.get_setting("scale"))/100
        q = urlencode({"model": model, "bg": background_color, "scale": scale})
        GLib.idle_add(self.webview.load_uri, urljoin("http://localhost:" + str(httpd.server_address[1]), f"?{q}"))
        httpd.serve_forever()

    def create_gtk_widget(self) -> Gtk.Widget:
        self.webview = WebKit.WebView()
        self.webview.connect("destroy", self.destroy)
        threading.Thread(target=self.__start_webserver).start()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        settings = self.webview.get_settings()
        settings.set_enable_webaudio(True)
        settings.set_media_playback_requires_user_gesture(False)
        self.webview.set_is_muted(False)
        self.webview.set_settings(settings)
        return self.webview

    def destroy(self, add=None):
        self.httpd.shutdown()
        self.webview = None

    def wait_emotions(self, object, result):
        value = self.webview.evaluate_javascript_finish(result)
        self._expressions_raw = json.loads(value.to_string())
        self._wait_js.set()

    def get_expressions(self): 
        if len(self._expressions_raw) > 0:
            return self._expressions_raw
        self._expressions_raw = []
        script = "get_expressions_json()"
        self.webview.evaluate_javascript(script, len(script), callback=self.wait_emotions)
        self._wait_js.wait(3)   
        return self._expressions_raw 

    def set_expression(self, expression : str):
        script = "set_expression('{}')".format(expression)
        self.webview.evaluate_javascript(script, len(script))
        pass   
           
    def speak(self, path: str, tts: TTSHandler, frame_rate: int):
        tts.stop()
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

    def _start_animation(self, amplitudes: list[float], frame_rate=10):
        max_amplitude = max(amplitudes)
        for amplitude in amplitudes:
            if self.stop_request:
                self.set_mouth(0)
                return
            self.set_mouth(amplitude/max_amplitude)
            sleep(1/frame_rate)

    def set_mouth(self, value):
        script = "set_mouth_y({})".format(value)
        self.webview.evaluate_javascript(script, len(script))

