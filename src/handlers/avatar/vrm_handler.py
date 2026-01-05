from urllib.parse import urlencode, urljoin
from http.server import HTTPServer, SimpleHTTPRequestHandler

from ...utility.system import open_folder
from ...handlers.avatar import AvatarHandler
from ...handlers.tts import TTSHandler
from ...handlers import HandlerDescription, ExtraSettings
import threading 
import os
import subprocess
import json
from pydub import AudioSegment
from livepng import LivePNG
from gi.repository import Gtk, WebKit, GLib
from time import sleep
from ...utility.strings import rgb_to_hex
from ...handlers import ExtraSettings

class VRMHandler(AvatarHandler):
    key = "vrm"
    _wait_js : threading.Event
    _wait_js2 : threading.Event
    _expressions_raw : list[str]
    _motions_raw : list[str]
    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self._expressions_raw = []
        self._motions_raw = []
        self._wait_js = threading.Event()
        self._wait_js2 = threading.Event()
        self.webview_path = os.path.join(path, "avatars", "vrm", "web")
        self.models_dir = os.path.join(self.webview_path, "models")
        self.webview = None

    def get_available_models(self): 
        file_list = []
        for root, _, files in os.walk(self.models_dir):
            for file in files:
                if file.endswith('.vrm'):
                    file_name = file.rstrip('.vrm')
                    relative_path = os.path.relpath(os.path.join(root, file), self.models_dir)
                    file_list.append((file_name, relative_path))
        return file_list

    def model_updated(self):
        self.settings_update()

    def get_model(self):
        m = self.get_setting("model", False)
        return "model.vrm" if m is None else m

    def get_extra_settings(self) -> list:
        widget = Gtk.Box()
        color = widget.get_style_context().lookup_color('window_bg_color')[1]
        default = rgb_to_hex(color.red, color.green, color.blue)

        return [
            {
                "key": "model",
                "title": _("VRM Model"),
                "description": _("VRM Model to use"),
                "type": "combo",
                "values": self.get_available_models(),
                "default": "model.vrm",
                "folder": os.path.abspath(self.models_dir),
                "refresh": lambda x: self.settings_update(),
                "update_settings": True
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
                "key": "light-color",
                "title": _("Light Color"),
                "description": _("Light color"),
                "type": "entry",
                "default": default,                                                                                                    
            },
            ExtraSettings.ButtonSetting("animations", _("Animations"), _("Put all the available animations in this folder"), lambda x : open_folder(os.path.join(self.webview_path, "animations")), icon="folder-symbolic", refresh=lambda x : self.refresh_animation_list()), 
        ]
    
    def refresh_animation_list(self):
        animation_folder = os.path.join(self.webview_path, "animations")
        animation_file = os.path.join(self.webview_path, "animation_list.json")
        files = [f for f in os.listdir(animation_folder) if f.lower().endswith('.bvh')] 
        files.sort() 
        with open(animation_file, 'w') as f:
            json.dump(files, f, indent=2)
        self.settings_update()
    
    def is_installed(self) -> bool:
        return os.path.isdir(self.webview_path)

    def install(self):
        subprocess.check_output(["git", "clone", "https://github.com/NyarchLinux/VRM-Web-Viewer", self.webview_path])
    
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
        scale = int(self.get_setting("scale", False, 100))/100
        q = urlencode({"model": "models/" + model, "bg": background_color, "scale": scale})
        GLib.idle_add(self.webview.load_uri, urljoin("http://localhost:" + str(httpd.server_address[1]), f"?{q}"))
        def update_expressions():
            sleep(2)
            self.get_expressions()
            self.get_motions()
            self.set_light_color()
        threading.Thread(target=update_expressions).start()
        httpd.serve_forever()

    def set_light_color(self):
        light_color = self.get_setting("light-color")
        script = f"set_light_color(\"{light_color}\")"
        self.webview.evaluate_javascript(script, len(script))

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

    def get_expressions_raw(self, allow_webview=True): 
        try:
            if len(self._expressions_raw) > 0:
                return self._expressions_raw
            if self.webview is None or not allow_webview:
                m = self.get_setting(self.get_model() + " expressions", False)
                return m if m is not None else []
            self._expressions_raw = []
            script = "get_expressions_json()"
            self.webview.evaluate_javascript(script, len(script), callback=self.wait_emotions)
            self._wait_js.wait(3)   
            self.set_setting(self.get_model() + " expressions", self._expressions_raw)
        except Exception as e:
            return []
        return self._expressions_raw 

    def convert_motion(self, motion: str):
        if motion in self.get_motions_raw():
            return motion
        for motion in self.get_motions_raw():
            name = self.get_setting("Expression " + motion, False)
            if name is not None:
                if name == motion:
                    return motion
        return None

    def convert_expression(self, expression: str):
        if expression in self.get_expressions_raw():
            return expression
        for expression in self.get_expressions_raw():
            name = self.get_setting("Expression " + expression, False)
            if name is not None:
                if name == expression:
                    return expression
        return None

    def get_expressions(self) -> list[str]:
        r = []
        for expression in self.get_expressions_raw():
            if expression is None:
                continue
            name = self.get_setting("Expression " + expression, False)
            if name is not None:
                r.append(name)
            else:
                r.append(expression)
        return r

    def get_motions(self) -> list[str]:
        r = []
        for motion in self.get_motions_raw():
            name = self.get_setting("Expression " + motion, False, None)
            if name is not None:
                r.append(name)
            else:
                if type(motion) is str:
                    r.append(motion)
        return r

    def get_motions_groups(self):
        if len(self._motions_raw) > 0:
            return self._motions_raw
        self._motions_raw = []
        script = "get_motions_json()"
        self.webview.evaluate_javascript(script, len(script), callback=self.wait_motions)
        self._wait_js2.wait(3)
        return self._motions_raw

    def get_motions_raw(self, allow_webview=True):
        if self.webview is None or not allow_webview:
            m = self.get_setting(self.get_model() + " motions", False)
            return m if m is not None else []
        r = []
        groups = self.get_motions_groups()
        r = groups
        if allow_webview:
            self.set_setting(self.get_model() + " motions", r)
        return r

    def wait_motions(self, object, result):
        value = self.webview.evaluate_javascript_finish(result)
        self._motions_raw = json.loads(value.to_string())
        self._wait_js2.set()

    def do_motion(self, motion : str):
        motion = self.convert_motion(motion)
        if motion is None:
            return
        script = "do_motion('{}')".format(motion)
        self.webview.evaluate_javascript(script, len(script))
        pass

    def set_expression(self, expression : str):
        exp = self.convert_expression(expression)
        if exp is None:
            return
        script = "set_expression('{}')".format(exp)
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

