import threading
import os
import json
import subprocess
from http.server import HTTPServer, SimpleHTTPRequestHandler
from time import sleep
from urllib.parse import urlencode, urljoin
from gi.repository import Gtk, WebKit, GLib
from ...utility.system import open_website

from ...utility.strings import rgb_to_hex
from .avatar import AvatarHandler
from ..tts import TTSHandler
from ...handlers import ExtraSettings
from ..handler import Handler

class Live2DHandler(AvatarHandler):
    key = "Live2D"
    # None until successfully read from the loaded model
    _expressions_raw : list[str] | None
    _motions_raw : list[dict] | None
    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self._expressions_raw = None
        self._motions_raw = None
        self.webview_path = os.path.join(path, "avatars", "live2d", "web")
        self.models_dir = os.path.join(self.webview_path, "models")
        self.webview = None

    def get_available_models(self): 
        file_list = []
        for root, _, files in os.walk(self.models_dir):
            for file in files:
                if file.endswith('.model3.json') or file.endswith('.model.json'):
                    suffix = '.model3.json' if file.endswith('.model3.json') else '.model.json'
                    file_name = file.removesuffix(suffix)
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
            ExtraSettings.ToggleSetting("reset-expression", _("Reset Expression"), _("Reset the expression at the end of the message"), False),
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
        GLib.idle_add(self.webview.load_uri, urljoin("http://localhost:" + str(httpd.server_address[1]), f"?{q}"))
        webview = self.webview
        def update_expressions():
            # The model loads asynchronously in the page, so poll until it answers
            while self.webview is webview:
                sleep(1)
                self.get_expressions_raw()
                self.get_motions_raw()
                if self._expressions_raw is not None and self._motions_raw is not None:
                    return
        threading.Thread(target=update_expressions).start()
        httpd.serve_forever()

    def create_gtk_widget(self) -> Gtk.Widget:
        self._expressions_raw = None
        self._motions_raw = None

        self.webview = WebKit.WebView()
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
        if hasattr(self, "httpd"):
            self.httpd.shutdown()
            self.webview = None

    def _run_js(self, script: str, callback=None):
        """Run script in the webview. WebKit may only be used from the main thread."""
        webview = self.webview
        if webview is None:
            return
        GLib.idle_add(lambda: webview.evaluate_javascript(script, len(script), callback=callback))

    def _query_js(self, script: str):
        """Run script in the webview and return its result parsed as JSON.

        Returns None if the page can't answer (e.g. the model is still loading).
        WebKit may only be used from the main thread, and the result arrives
        through the main loop, so this must be called from another thread.
        """
        if self.webview is None or threading.current_thread() is threading.main_thread():
            return None
        done = threading.Event()
        result = [None]
        def on_finished(webview, task):
            try:
                result[0] = json.loads(webview.evaluate_javascript_finish(task).to_string())
            except Exception:
                pass
            done.set()
        self._run_js(script, on_finished)
        done.wait(3)
        return result[0]

    def _store_model_cache(self, kind: str, value):
        """Persist what the model provides, for the Expression Manager to list
        while no webview is loaded."""
        key = self.get_model() + " " + kind
        if self.get_setting(key, False) != value:
            # Bypass AvatarHandler.set_setting: a cache update must not
            # mark the avatar for reloading
            Handler.set_setting(self, key, value)

    def get_expressions_raw(self, allow_webview=True):
        if self._expressions_raw is not None:
            return self._expressions_raw
        stored = self.get_setting(self.get_model() + " expressions", False) or []
        if not allow_webview:
            return stored
        expressions = self._query_js("get_expressions_json()")
        if expressions is None:
            return stored
        self._expressions_raw = expressions
        self._store_model_cache("expressions", expressions)
        return expressions

    def convert_motion(self, motion: str):
        motions_raw = self.get_motions_raw()
        if motion in motions_raw:
            return motion
        for raw in motions_raw:
            if self.get_setting("Expression " + raw, False) == motion:
                return raw
        return None

    def convert_expression(self, expression: str):
        expressions_raw = self.get_expressions_raw()
        if expression in expressions_raw:
            return expression
        for raw in expressions_raw:
            if self.get_setting("Expression " + raw, False) == expression:
                return raw
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
        if self._motions_raw is None:
            self._motions_raw = self._query_js("get_motions_json()")
        return self._motions_raw or []

    def get_motions_raw(self, allow_webview=True):
        groups = self.get_motions_groups() if allow_webview else []
        if not groups:
            return self.get_setting(self.get_model() + " motions", False) or []
        r = []
        for group in groups:
            for motion in group["motions"]:
                if "name" in motion and motion["name"] is not None:
                    r.append(motion["name"])
                else:
                    f = motion["file"]
                    if f is None:
                        continue
                    r.append(f.split("/")[-1].split(".")[0])
        self._store_model_cache("motions", r)
        return r

    def get_motion_file(self, motion: str):
        for group in self.get_motions_groups():
            if motion == group["groupName"]:
                return motion
            for motion2 in group["motions"]:
                if motion == motion2["name"] or motion == motion2["file"].split("/")[-1].split(".")[0]:
                    return motion2["file"]
        return None

    def do_motion(self, motion : str):
        motion = self.convert_motion(motion)
        if motion is None:
            return
        motion = self.get_motion_file(motion)
        if motion is None:
            return
        self._run_js("do_motion('{}')".format(motion))
        pass

    def set_expression(self, expression : str):
        exp = self.convert_expression(expression)
        if exp is None:
            return
        self._run_js("set_expression('{}')".format(exp))
           
    def speak_with_tts(self, text: str, tts: TTSHandler, translator):
        super().speak_with_tts(text, tts, translator)
        if self.get_setting("reset-expression", False, False):
            self._run_js("model_proxy.internalModel.motionManager.expressionManager?.resetExpression()")

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
        script = "set_mouth_y({})".format(value)
        self.webview.evaluate_javascript(script, len(script))
