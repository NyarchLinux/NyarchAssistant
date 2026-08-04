from gettext import gettext as _

from gi.repository import Adw, GObject, Gtk, Pango


MAX_VISIBLE_OUTPUT_CHARS = 8000


class CommandSessionActionWidget(Gtk.ListBox):
    """Compact result widget for terminal session reads and key presses."""

    __gsignals__ = {
        "terminal-clicked": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(
        self,
        action: str,
        output: str | None,
        *,
        session_id: str | None = None,
        keys: list | None = None,
    ):
        super().__init__(
            css_classes=["boxed-list"],
            selection_mode=Gtk.SelectionMode.NONE,
            margin_top=10,
            margin_bottom=10,
            margin_end=10,
        )
        self.action = action
        self.output = output or ""
        self.session_id = session_id
        self.keys = keys or []
        self.metadata, self.terminal_output = self._parse_output(self.output)
        self.active_session_id = (
            self.metadata.get("Session ID") or self.session_id
        )

        row = self._build_row()
        self._add_terminal_button(row)
        self._add_status_icon(row)
        self.append(row)

    @staticmethod
    def _parse_output(output: str) -> tuple[dict[str, str], str]:
        metadata = {}
        terminal_lines = []
        reading_output = False

        for line in output.splitlines():
            if reading_output:
                terminal_lines.append(line)
            elif line == "Output:":
                reading_output = True
            elif ": " in line:
                key, value = line.split(": ", 1)
                metadata.setdefault(key.strip(), value.strip())

        terminal_output = "\n".join(terminal_lines)
        if terminal_output == "(no new output)":
            terminal_output = ""
        return metadata, terminal_output

    def _build_row(self):
        title, icon_name = {
            "read": (_("Read terminal output"), "view-refresh-symbolic"),
            "send_keys": (_("Send terminal keys"), "input-keyboard-symbolic"),
        }.get(self.action, (_("Terminal session"), "gnome-terminal-symbolic"))
        subtitle = self._build_subtitle()

        if self.action == "read" and self.terminal_output:
            row = Adw.ExpanderRow(
                title=title,
                subtitle=subtitle,
                icon_name=icon_name,
                expanded=False,
            )
            row.add_row(self._build_output_view())
            return row

        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.set_icon_name(icon_name)
        return row

    def _build_subtitle(self) -> str:
        error = self.metadata.get("Error")
        if error:
            return error

        parts = []
        session_id = self.metadata.get("Session ID") or self.session_id
        if session_id:
            parts.append(session_id)

        state = self.metadata.get("Session State")
        if state:
            parts.append(
                {
                    "running": _("Running"),
                    "exited": _("Exited"),
                }.get(state, state)
            )

        if self.action == "read":
            if self.terminal_output:
                parts.append(
                    _("{count} characters").format(count=len(self.terminal_output))
                )
            else:
                parts.append(_("No new output"))
        elif self.action == "send_keys":
            key_text = " + ".join(str(key).upper() for key in self.keys)
            if key_text:
                parts.append(
                    key_text if len(key_text) <= 80 else key_text[:79] + "…"
                )
            elif self.metadata.get("Bytes Written"):
                parts.append(
                    _("{count} bytes written").format(
                        count=self.metadata["Bytes Written"]
                    )
                )

        return " · ".join(parts)

    def _build_output_view(self):
        display_output = self.terminal_output[-MAX_VISIBLE_OUTPUT_CHARS:]
        if len(display_output) < len(self.terminal_output):
            display_output = "…\n" + display_output

        label = Gtk.Label(
            label=display_output,
            css_classes=["monospace"],
            selectable=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.CHAR,
            xalign=0,
            margin_top=8,
            margin_bottom=8,
            margin_start=8,
            margin_end=8,
        )
        scrolled = Gtk.ScrolledWindow(
            child=label,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            propagate_natural_height=True,
            max_content_height=180,
        )
        scrolled.add_css_class("expander-inset-content")
        return scrolled

    def _add_status_icon(self, row):
        succeeded = self.metadata.get("Status", "success") == "success"
        icon = Gtk.Image(
            icon_name=(
                "emblem-default-symbolic"
                if succeeded
                else "dialog-error-symbolic"
            ),
            tooltip_text=_("Completed") if succeeded else _("Failed"),
        )
        icon.add_css_class("success" if succeeded else "error")
        row.add_suffix(icon)

    def _add_terminal_button(self, row):
        if (
            self.active_session_id is None
            or self.metadata.get("Session State") == "exited"
        ):
            self.terminal_button = None
            return

        self.terminal_button = Gtk.Button(
            icon_name="gnome-terminal-symbolic",
            tooltip_text=_("Open in Terminal"),
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
        )
        self.terminal_button.connect("clicked", self._on_terminal_clicked)
        row.add_suffix(self.terminal_button)

    def _on_terminal_clicked(self, _button):
        if self.active_session_id is not None:
            self.emit("terminal-clicked", self.active_session_id)

    def set_active_session_available(self, available: bool):
        """Hide the terminal action when its backing session is unavailable."""
        if not available:
            self.active_session_id = None
        if self.terminal_button is not None:
            self.terminal_button.set_visible(available)
