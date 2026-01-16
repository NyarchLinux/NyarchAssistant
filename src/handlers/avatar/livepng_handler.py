import os 
import subprocess 
import threading
from livepng import LivePNG
from livepng.constants import FilepathOutput
from gi.repository import Gtk, GdkPixbuf, Gdk

from .avatar import AvatarHandler

class LivePNGHandler(AvatarHandler):
    key = "LivePNG"
    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self.models_path = os.path.join(path, "avatars", "livepng", "models")
        if not os.path.isdir(self.models_path):
            os.makedirs(self.models_path)
    
    def get_extra_settings(self) -> list:
        styles, default = self.get_styles_list() 
        return [ 
            {
                "key": "model",
                "title": _("LivePNG Model"),
                "description": _("LivePNG Model to use"),
                "type": "combo",
                "values": self.get_available_models(),
                "default": "kurisu/model.json",
                "folder": os.path.abspath(self.models_path),
                "update_settings": True,
                "refresh": lambda x: self.settings_update(),
            },
            {
             "key": "fps",
                "title": _("Lipsync Framerate"),
                "description": _("Maximum amount of frames to generate for lipsync"),
                "type": "range",
                "min": 5,
                "max": 30,
                "default": 10,
                "round-digits": 0
            },
            {
                "key": "style",
                "title": _("LivePNG model style"),
                "description": _("Choose the style of the model for the specified one"),
                "type": "combo",
                "values": styles,
                "default": default
            }
        ]

    def get_styles_list(self) -> tuple[list, str]:
        path = self.get_setting("model", False)
        if not type(path) is str:
            return ([], "")
        try:
            self.model = LivePNG(path, output_type=FilepathOutput.LOCAL_PATH)
        except Exception as e:
            return tuple()
        return ([(style, style) for style in self.model.get_styles()], self.model.get_default_style().name)
    
    def get_available_models(self) -> list[tuple[str, str]]:
        dirs = os.listdir(self.models_path)
        result = []
        for dir in dirs:
            if not os.path.isdir(os.path.join(self.models_path, dir)):
                continue
            jsonpath = os.path.join(self.models_path, dir, "model.json")
            if not os.path.isfile(jsonpath):
                continue
            try:
                model = LivePNG(jsonpath)
                result.append((model.get_name(), jsonpath))
            except Exception as e:
                print(e)
        return result

    def create_gtk_widget(self) -> Gtk.Widget:
        overlay = Gtk.Overlay()
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_hexpand(True)
        self.drawing_area.set_vexpand(True)
        def on_draw(widget, cr, width, height):
            if self.pixbuf is None:
                return
            # Get the original dimensions of the image
            original_width = self.pixbuf.get_width()
            original_height = self.pixbuf.get_height()

            # Calculate the scaling factors while maintaining the aspect ratio
            if original_width / original_height > width / height:
                # Image is wider than the container
                scale_factor = width / original_width
            else:
                # Image is taller than the container
                scale_factor = height / original_height

            # Calculate the new dimensions
            new_width = int(original_width * scale_factor)
            new_height = int(original_height * scale_factor)

            # Scale the image to fit the drawing area
            scaled_pixbuf = self.pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
            
            x = (width - new_width) / 2
            y = (height - new_height) / 2
            # Draw the scaled image
            Gdk.cairo_set_source_pixbuf(cr, scaled_pixbuf, x, y)
            cr.paint()
        self.drawing_area.set_draw_func(on_draw)
        self.__load_model()
        return self.drawing_area

    def set_expression(self, expression: str):
        self.model.set_current_expression(expression)

    def speak(self, path, tts, frame_rate):
        tts.stop()
        self.model.stop()
        t1 = threading.Thread(target=self.model.speak, args=(path, True, False, frame_rate, True, False))
        t2 = threading.Thread(target=tts.playsound, args=(path, ))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    def stop(self):
        self.model.stop()

    def _start_animation(self, path, frame_rate):
        self.model.speak(path, True, False, frame_rate, True, False)

    def __load_model(self):
        path = self.get_setting("model")
        if not type(path) is str:
            return
        self.model = LivePNG(path, output_type=FilepathOutput.LOCAL_PATH)
        self.model.set_current_style(self.get_setting("style"))
        t = threading.Thread(target=self.preacache_images)
        t.start()
        self.model.subscribe_callback(self.__on_update)
        self.__on_update(self.model.get_current_image())

    def __on_update(self, frame:str):
        if frame in self.cachedpixbuf:
            pix = self.cachedpixbuf[frame]
        else:
            pix = self.__load_image(frame)
        self.pixbuf = pix
        self.drawing_area.queue_draw()

    def preacache_images(self):
        self.cachedpixbuf = {}
        for image in self.model.get_images_list():
            self.cachedpixbuf[image] = self.__load_image(image)

    def get_expressions(self) -> list[str]:
        return [expression for expression in self.model.get_expressions()]
        
    def __load_image(self, image):
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(filename=image, width=2000,height=-1, preserve_aspect_ratio=True )

    def is_installed(self) -> bool:
        return len(self.get_available_models()) > 0

    def install(self):
        subprocess.check_output(["wget", "-P", os.path.join(self.models_path), "http://mirror.nyarchlinux.moe/models.tar.gz"])
        subprocess.check_output(["tar", "-xf", os.path.join(self.models_path, "models.tar.gz"), "-C", self.models_path, "--strip-components=1"])
        subprocess.Popen(["rm", os.path.join(self.models_path, "models.tar.gz")])
    
    def set_setting(self, key:str, value):
        """Overridden version of set_setting that also updates the default style setting when the model is changed"""
        super().set_setting(key, value)
        if key == "model":
            self.set_setting("style", self.get_styles_list()[1])

