import threading
import os
import json
import subprocess
import contextlib
from http.server import HTTPServer, SimpleHTTPRequestHandler
from time import sleep
from urllib.parse import urlencode, urljoin
from gi.repository import Gtk, WebKit, GLib
from ...utility.system import open_website

from ...utility.strings import rgb_to_hex
from .avatar import AvatarHandler
from ..tts import TTSHandler
from ...handlers import ExtraSettings

class Live2DHandler(AvatarHandler):
    key = "Live2D"
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
        self.webview_path = os.path.join(path, "avatars", "live2d", "web")
        self.models_dir = os.path.join(self.webview_path, "models")
        self.webview = None
        self.httpd = None
        self._server_thread = None
        self._destroyed = False

    def get_available_models(self): 
        file_list = []
        for root, _, files in os.walk(self.models_dir):
            for file in files:
                if file.endswith('.model3.json') or file.endswith('.model.json'):
                    file_name = file.rstrip('.model3.json').rstrip('.model.json')
                    relative_path = os.path.relpath(os.path.join(root, file), self.models_dir)
                    file_list.append((file_name, relative_path))
        return file_list

    def model_updated(self):
        self.settings_update()

    def get_model(self):
        m = self.get_setting("model", False)
        return "Arch/arch chan model0.model3.json" if m is None else m

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
                "key": "scale",
                "title": _("Zoom Model"),
                "description": _("Zoom the Live2D model"),
                "type": "range",
                "min": 5,
                "max": 300,
                "default": 100,
                "round-digits": 0
            },
            ExtraSettings.NestedSetting("em", "Expression Manager", "Rename expressions in order to make them more understandable for the LLM. Avoid duplicates.", 
                self.get_expression_manager()
            )
        ]
    def get_expression_manager(self):
        r = []
        li = (self.get_expressions_raw(allow_webview=False) + self.get_motions_raw(allow_webview=False))
        if len(li) == 0:
            return [
                ExtraSettings.ButtonSetting("no_entry", "You need to reload the model first", "Close and reopen the settings after changing a model in order to use the expression manager", lambda x: open_website("https://wiki.nyarchlinux.moe/nyarchasssistant",), "Nyarch Wiki")
            ]
        for expression in li:
            if type(expression) is not str:
                continue
            r.append(
                ExtraSettings.EntrySetting(
                    "Expression " + expression,
                    expression,
                    "Rename " + expression,
                    expression
                )
            )
        return r

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
        if self.webview is not None and not self._destroyed:
            GLib.idle_add(self.webview.load_uri, urljoin("http://localhost:" + str(httpd.server_address[1]), f"?{q}"))
        def update_expressions():
            sleep(2)
            if self._destroyed:
                return
            self.get_expressions()
            self.get_motions()
        threading.Thread(target=update_expressions, daemon=True).start()
        try:
            httpd.serve_forever()
        finally:
            with contextlib.suppress(Exception):
                httpd.server_close()

    def create_gtk_widget(self) -> Gtk.Widget:
        if self.webview is not None and not self._destroyed:
            self.destroy(self.webview, force=True)
        self._destroyed = False
        self._wait_js = threading.Event()
        self._wait_js2 = threading.Event()
        self.webview = WebKit.WebView()
        self.webview.connect("destroy", self.destroy)
        self._server_thread = threading.Thread(target=self.__start_webserver, daemon=True)
        self._server_thread.start()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        settings = self.webview.get_settings()
        settings.set_enable_webaudio(True)
        settings.set_media_playback_requires_user_gesture(False)
        self.webview.set_is_muted(False)
        self.webview.set_settings(settings)
        return self.webview

    def destroy(self, widget=None, force=False):
        if widget is not None and self.webview is not None and widget is not self.webview and not force:
            return
        if self._destroyed and not force:
            return
        self._destroyed = True
        httpd = self.httpd
        if httpd is not None:
            with contextlib.suppress(Exception):
                httpd.shutdown()
            with contextlib.suppress(Exception):
                httpd.server_close()
            self.httpd = None

        webview = self.webview
        if webview is not None:
            webview.terminate_web_process()
        self.webview = None

    def wait_emotions(self, object, result):
        if self.webview is None or self._destroyed:
            self._wait_js.set()
            return
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
            name = self.get_setting("Expression " + motion, False)
            if name is not None:
                r.append(name)
            elif type(motion) is str:
                    r.append(motion)
        return r

    def get_motions_groups(self):
        if self.webview is None or self._destroyed:
            return []
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
        for group in groups:
            for motion in group["motions"]:
                if "name" in motion and motion["name"] is not None:
                    r.append(motion["name"])
                else:
                    f = motion["file"]
                    if f is None:
                        continue
                    r.append(f.split("/")[-1].split(".")[0])
        if allow_webview:
            self.set_setting(self.get_model() + " motions", r)
        return r

    def get_motion_file(self, motion: str):
        for group in self.get_motions_groups():
            if motion == group["groupName"]:
                return motion
            for motion2 in group["motions"]:
                if motion == motion2["name"] or motion == motion2["file"].split("/")[-1].split(".")[0]:
                    return motion2["file"]
        return None

    def wait_motions(self, object, result):
        if self.webview is None or self._destroyed:
            self._wait_js2.set()
            return
        value = self.webview.evaluate_javascript_finish(result)
        self._motions_raw = json.loads(value.to_string())
        self._wait_js2.set()

    def do_motion(self, motion : str):
        if self.webview is None or self._destroyed:
            return
        motion = self.convert_motion(motion)
        if motion is None:
            return
        motion = self.get_motion_file(motion)
        if motion is None:
            return
        script = "do_motion('{}')".format(motion)
        self.webview.evaluate_javascript(script, len(script))
        pass

    def set_expression(self, expression : str):
        if self.webview is None or self._destroyed:
            return
        exp = self.convert_expression(expression)
        if exp is None:
            return
        script = "set_expression('{}')".format(exp)
        self.webview.evaluate_javascript(script, len(script))
        pass   
           
    def speak(self, path: str, tts: TTSHandler, frame_rate: int):
        # LivePNG-based lipsync: model.speak drives frame-by-frame mouth animation
        # from the complete audio file.
        from livepng import LivePNG as _LivePNG
        from pydub import AudioSegment
        tts.stop()
        audio = AudioSegment.from_file(path)
        amplitudes = _LivePNG.calculate_amplitudes(audio.frame_rate, audio.get_array_of_samples(), frame_rate=frame_rate)
        t1 = threading.Thread(target=self._start_animation, args=(amplitudes, frame_rate))
        t2 = threading.Thread(target=tts.playsound, args=(path,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    def set_mouth(self, value):
        
        if self.webview is None or self._destroyed:
            print("Webview not available, cannot set mouth value")
            return
        script = "set_mouth_y({})".format(value)
        self.webview.evaluate_javascript(script, len(script))

