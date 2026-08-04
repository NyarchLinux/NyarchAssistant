import gi
from gi.repository import Gtk, Adw, GLib, Pango, Gdk
import sys

from gi.repository.GObject import GObject
if sys.platform != 'win32':
    gi.require_version('Vte', '3.91')
    from gi.repository import Vte

if sys.platform != 'win32':    
    class Terminal(Vte.Terminal):
        def __init__(self, script:list):
            super().__init__(css_classes=["terminal"])
            self.set_font(Pango.FontDescription.from_string("Monospace 12"))
            self.set_clear_background(False)
            pty = Vte.Pty.new_sync(Vte.PtyFlags.DEFAULT, None)
            self.set_pty(pty)
            pty.spawn_async(
                GLib.get_current_dir(),
                script,
                None,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                None,
                None,
                None
            )
            key_controller = Gtk.EventControllerKey()
            key_controller.connect("key-pressed", self.on_key_press)
            self.add_controller(key_controller)

        def on_key_press(self, controller, keyval, keycode, state):
            ctrl = state & Gdk.ModifierType.CONTROL_MASK
            shift = state & Gdk.ModifierType.SHIFT_MASK
            if ctrl and keyval == Gdk.KEY_c:
                self.copy_clipboard()
                return True
            elif ctrl and keyval == Gdk.KEY_v:
                self.paste_clipboard()
                return True
            return False
        def get_output(self):
            txt = self.get_text_format(Vte.Format.TEXT)
            return txt 

    class SessionTerminal(Vte.Terminal):
        """Interactive VTE view backed by an existing command session."""

        def __init__(self, session):
            super().__init__(css_classes=["terminal"])
            self.session = session
            self._detached = False
            self._last_size = None
            self.set_font(Pango.FontDescription.from_string("Monospace 12"))
            self.set_clear_background(False)
            self.set_scrollback_lines(10000)
            self.set_size(120, 40)

            self.connect("commit", self._on_commit)
            key_controller = Gtk.EventControllerKey()
            key_controller.connect("key-pressed", self._on_key_press)
            self.add_controller(key_controller)

            self._listener_id, snapshot = self.session.subscribe_terminal_output(
                self._queue_output
            )
            if snapshot:
                self.feed(snapshot.encode("utf-8"))
            self._tick_callback_id = self.add_tick_callback(self._sync_size)

        def _queue_output(self, text):
            if not self._detached:
                GLib.idle_add(self._feed_output, text)

        def _feed_output(self, text):
            if self._detached:
                return False
            self.feed(text.encode("utf-8"))
            return False

        def _on_commit(self, _terminal, text, _size):
            if self._detached:
                return
            try:
                self.session.write_text(text)
            except Exception:
                self.detach()

        def _on_key_press(self, _controller, keyval, _keycode, state):
            ctrl = state & Gdk.ModifierType.CONTROL_MASK
            shift = state & Gdk.ModifierType.SHIFT_MASK
            if ctrl and shift and keyval == Gdk.KEY_C:
                self.copy_clipboard()
                return True
            if ctrl and shift and keyval == Gdk.KEY_V:
                self.paste_clipboard()
                return True
            return False

        def _sync_size(self, _widget, _frame_clock):
            if self._detached:
                return False
            size = (self.get_row_count(), self.get_column_count())
            if size != self._last_size:
                self._last_size = size
                try:
                    self.session.resize(*size)
                except Exception:
                    self.detach()
                    return False
            return True

        def detach(self):
            if self._detached:
                return
            self._detached = True
            self.session.unsubscribe_terminal_output(self._listener_id)
            if self._tick_callback_id is not None:
                self.remove_tick_callback(self._tick_callback_id)
                self._tick_callback_id = None

        def get_output(self):
            return self.get_text_format(Vte.Format.TEXT)
else:
    class Terminal(Gtk.Box):
        def __init__(self, script:list):
            super().__init__()
            self.append(Gtk.Label(label="Terminal not supported"))

        def get_output(self):
            return ""

    class SessionTerminal(Terminal):
        def __init__(self, _session):
            super().__init__([])

        def detach(self):
            pass

class TerminalDialog(Adw.Dialog):
    def __init__(self, confirm_output=True, **kwargs):
        super().__init__(**kwargs)

        # Accessibility
        self.set_title("Terminal") 
        self.connect("close-attempt", self.closing_terminal)
        self.connect("closed", self._on_closed)
        self.set_can_close(not confirm_output)
        self.output_func = lambda x: x
        # Toolbar View
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_css_class("osd")

        # Header Bar
        header_bar = Adw.HeaderBar()
        toolbar_view.add_top_bar(header_bar)

        # Scrolled Window
        self.terminal_scroller = Gtk.ScrolledWindow(
            propagate_natural_height=True,
            propagate_natural_width=True
        )
        toolbar_view.set_content(self.terminal_scroller)
        self.set_child(toolbar_view)

    def load_terminal(self, command:list[str]):
        self.set_terminal(Terminal(command))

    def save_output_func(self, output_func):
        self.output_func = output_func

    def set_terminal(self, terminal):
        if hasattr(self, "terminal") and hasattr(self.terminal, "detach"):
            self.terminal.detach()
        self.terminal = terminal
        self.terminal_scroller.set_child(terminal)

    def load_session(self, session):
        self.set_terminal(SessionTerminal(session))

    def close_window(self,dialog, response):
        self.set_can_close(True)
        self.close()
        if response == "save": 
            self.output_func(self.terminal.get_output())
        else:
            self.output_func(None)

    def closing_terminal(self, *args):
        if self.get_can_close():
            return False
        dialog = Adw.AlertDialog(body="Do you want to send the output of the terminal to the LLM to get a response?\nNote: Only the visible text will be sent as response", title="Send output?")
        dialog.add_response("save", "Send output")
        dialog.add_response("close", "Discard output")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self.close_window)
        dialog.present()
        return True 

    def _on_closed(self, *_args):
        if hasattr(self, "terminal") and hasattr(self.terminal, "detach"):
            self.terminal.detach()
