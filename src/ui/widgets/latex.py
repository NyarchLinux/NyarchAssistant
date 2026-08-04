from gi.repository import Gtk, GLib, Gdk
from matplotlib.figure import Figure
from matplotlib.backends.backend_gtk4agg import FigureCanvasGTK4Agg


# Cache measured (width, height) for a (latex, size, color_rgb) tuple so that
# zoom rebuilds and re-renders of the same equation avoid the expensive
# matplotlib draw used for text-extent measurement.
_DIM_CACHE = {}


def _color_rgb(color):
    # matplotlib expects an (r, g, b) tuple; Gdk.RGBA exposes .red/.green/.blue.
    return (color.red, color.green, color.blue)


def measure_latex(latex: str, size: int, color) -> tuple[int, int]:
    """Return the (width, height) pixel size of a rendered equation.

    Cached per (latex, size, color_rgb). Building the figure / drawing once per
    key is the only unavoidable matplotlib cost; everything else reuses this.
    """
    key = (latex, size, _color_rgb(color))
    cached = _DIM_CACHE.get(key)
    if cached is not None:
        return cached
    fig = Figure()
    fig.patch.set_alpha(0)
    ax = fig.add_subplot()
    txt = ax.text(0.5, 0.5, r'$' + latex + r'$', fontsize=size, ha='center',
                  va='center', color=key[2])
    ax.axis('off')
    fig.canvas.draw()
    ext = txt.get_window_extent()
    dims = (int(ext.width), int(ext.height))
    _DIM_CACHE[key] = dims
    return dims


class LatexCanvas(FigureCanvasGTK4Agg):
    def __init__(self, latex: str, size: int, color, inline: bool = False) -> None:
        color_rgb = _color_rgb(color)
        dims = measure_latex(latex, size, color)
        self.dims = dims
        fig = Figure()
        fig.patch.set_alpha(0)
        ax = fig.add_subplot()
        ax.text(0.5, 0.5, r'$' + latex + r'$', fontsize=size, ha='center',
                va='center', color=color_rgb)
        ax.axis('off')
        fig.tight_layout()
        super().__init__(fig)
        w, h = dims
        self.set_hexpand(False)
        self.set_vexpand(False)
        if inline:
            self.set_halign(Gtk.Align.START)
            self.set_valign(Gtk.Align.END)
            self.set_size_request(w, h)
        else:
            self.set_hexpand(True)
            self.set_size_request(w, h + int(h * (0.1)))
        self.set_css_classes(['latex_renderer'])


class _LazyLatexMixin:
    """Shared lazy-build / zoom-rebuild machinery for the latex containers.

    The heavy matplotlib + GTK canvas widget is created on idle after the
    surrounding message has painted, so chat switches stay responsive. Subclasses
    must implement `_attach_canvas(canvas)` and `_detach_canvas()`.
    """

    def _lazy_init(self, latex: str, size: int, inline: bool) -> None:
        self.latex = latex
        self.size = size
        self.inline = inline
        self.picture = None
        self._build_id = None
        # This color is used only to size the placeholder synchronously — text
        # extents don't depend on color. The actual render color is re-read in
        # _idle_build, because get_color() returns a default white/black until
        # the widget is parented and its theme context is available.
        self.color = self.get_style_context().get_color()
        # dims are known synchronously from the (cached) measurement, so layout
        # works immediately even before the canvas widget exists.
        self.dims = measure_latex(latex, size, self.color)

    def _schedule_build(self) -> None:
        if self._build_id is not None:
            return
        self._build_id = GLib.idle_add(self._idle_build)

    def _cancel_build(self) -> None:
        if self._build_id is not None:
            GLib.source_remove(self._build_id)
            self._build_id = None

    def _idle_build(self) -> bool:
        self._build_id = None
        # Widget was torn down (chat switched) before we got to build it.
        if self.get_root() is None:
            return False
        # Re-read the foreground now that the widget is parented/realized, so
        # the rendered equation matches the theme's actual text color instead
        # of the default white/black captured at construction time.
        self.color = self.get_style_context().get_color()
        try:
            canvas = LatexCanvas(self.latex, self.size, self.color, inline=self.inline)
        except Exception:
            return False
        self._detach_canvas()
        self.picture = canvas
        self.dims = canvas.dims
        self._attach_canvas(canvas)
        return False

    def rebuild_at_size(self, size: int) -> None:
        """Rebuild the canvas at a new size, replacing the current one.

        If the first build is still pending, just update the target size — the
        pending idle build picks it up.
        """
        self.size = size
        self.dims = measure_latex(self.latex, size, self.color)
        if self.picture is None and self._build_id is not None:
            return  # pending build will use the new size
        self._cancel_build()
        self._build_id = GLib.idle_add(self._idle_build)

    # --- hooks for subclasses ---
    def _attach_canvas(self, canvas) -> None:
        raise NotImplementedError

    def _detach_canvas(self) -> None:
        raise NotImplementedError


class InlineLatex(_LazyLatexMixin, Gtk.Box):
    def __init__(self, latex: str, size: int) -> None:
        super().__init__()
        self._lazy_init(latex, size, inline=True)
        self.placeholder = Gtk.Box()
        self.append(self.placeholder)
        self._schedule_build()

    def _attach_canvas(self, canvas) -> None:
        if canvas.dims[0] > 300:
            scroll = Gtk.ScrolledWindow(
                vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, propagate_natural_width=True)
            scroll.set_child(canvas)
            scroll.set_size_request(300, -1)
            scroll.set_hexpand(False)
            self.append(scroll)
            self._scroll = scroll
        else:
            self.append(canvas)

    def _detach_canvas(self) -> None:
        # Remove every child except the placeholder.
        while self.get_first_child() is not None:
            self.remove(self.get_first_child())
        self.append(self.placeholder)
        self._scroll = None

    def update_zoom(self, size: int) -> None:
        self.rebuild_at_size(size)


class DisplayLatex(_LazyLatexMixin, Gtk.Box):
    def __init__(self, latex: str, base_size: int, cache_dir: str, inline: bool = False) -> None:
        super().__init__()
        self.cachedir = cache_dir
        self.base_size = base_size
        self._manual_offset = 0
        self._lazy_init(latex, base_size, inline=inline)
        self.scroll = None
        if not inline:
            self.scroll = Gtk.ScrolledWindow(
                vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, propagate_natural_width=True)
            self.placeholder = Gtk.Box()
            self.scroll.set_child(self.placeholder)
            self.create_control_box()
            self.controller()
            overlay = Gtk.Overlay()
            overlay.set_child(self.scroll)
            overlay.add_overlay(self.control_box)
            self.overlay = overlay
            self.append(overlay)
        else:
            self.placeholder = Gtk.Box()
            self.append(self.placeholder)
        self._schedule_build()

    def _attach_canvas(self, canvas) -> None:
        if self.scroll is not None:
            self.scroll.set_child(canvas)
        else:
            self.append(canvas)

    def _detach_canvas(self) -> None:
        if self.scroll is not None:
            self.scroll.set_child(self.placeholder)
        else:
            while self.get_first_child() is not None:
                self.remove(self.get_first_child())
            self.append(self.placeholder)

    def zoom_in(self, *_):
        self._manual_offset += 10
        self.rebuild_at_size(max(10, self.base_size + self._manual_offset))

    def zoom_out(self, *_):
        if self.base_size + self._manual_offset <= 10:
            return
        self._manual_offset -= 10
        self.rebuild_at_size(max(10, self.base_size + self._manual_offset))

    def update_zoom(self, zoom: int) -> None:
        self.base_size = max(10, int(16 * zoom / 100))
        self.rebuild_at_size(max(10, self.base_size + self._manual_offset))

    def create_control_box(self):
        self.control_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.END, css_classes=["flat"], visible=False)
        self.copy_button = Gtk.Button(halign=Gtk.Align.START, css_classes=["flat", "accent"], icon_name="edit-copy-symbolic", valign=Gtk.Align.START)
        self.copy_button.connect("clicked", self.copy_button_clicked)

        self.zoom_out_button = Gtk.Button(halign=Gtk.Align.START, css_classes=["flat", "error"], icon_name="zoom-out-symbolic", valign=Gtk.Align.START)
        self.zoom_out_button.connect("clicked", self.zoom_out)
        self.control_box.append(self.zoom_out_button)

        self.zoom_in_button = Gtk.Button(halign=Gtk.Align.START, css_classes=["flat", "success"], icon_name="zoom-in-symbolic", valign=Gtk.Align.START)
        self.zoom_in_button.connect("clicked", self.zoom_in)
        self.control_box.append(self.zoom_in_button)

        self.control_box.append(self.copy_button)

    def controller(self):
        ev = Gtk.EventControllerMotion.new()
        ev.connect("enter", lambda x, y, data: self.control_box.set_visible(True))
        ev.connect("leave", lambda data: self.control_box.set_visible(False))
        self.add_controller(ev)

    def copy_button_clicked(self, widget):
        display = Gdk.Display.get_default()
        if display is None:
            return
        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self.latex))
        self.copy_button.set_icon_name("object-select-symbolic")
