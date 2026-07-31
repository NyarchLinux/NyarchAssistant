"""ModeButton — input-bar switcher for the active Mode.

A ``Gtk.MenuButton`` whose label shows ``<icon> <Mode Name> ▾`` and whose
popover lists every Mode (icon + name + description, with an inline edit
button) plus a "Create a new Mode" footer row. Selecting a mode activates it
via :class:`ModeManager`; the edit buttons and the footer open
:class:`ModeEditorDialog`.
"""

import gettext

from gi.repository import Gtk, Adw, GObject

from ...modes import DEFAULT_MODE_ICON

_ = gettext.gettext


class ModeButton(Gtk.MenuButton):
    """Menu button that switches the active Mode and edits Modes."""

    __gsignals__ = {
        # Emitted after the active mode changed and settings were reloaded,
        # so listeners (e.g. the chat tab) can refresh dependent UI.
        "mode-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, controller, window):
        super().__init__(css_classes=["flat"])
        self.controller = controller
        self.window = window
        self.set_valign(Gtk.Align.CENTER)

        # Label content: icon + name + down arrow.
        self.label_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.icon = Gtk.Image(pixel_size=16)
        self.name_label = Gtk.Label(label="")
        self.arrow = Gtk.Image(icon_name="pan-down-symbolic")
        self.arrow.add_css_class("dim-label")
        self.label_box.append(self.icon)
        self.label_box.append(self.name_label)
        self.label_box.append(self.arrow)
        self.set_child(self.label_box)
        self.set_always_show_arrow(False)

        # Popover holding the mode list.
        self.popover = Gtk.Popover()
        self.set_popover(self.popover)
        self.popover.connect("closed", lambda _p: None)

        self.refresh()

    # ------------------------------------------------------------------ #
    # Building the popover
    # ------------------------------------------------------------------ #
    def _build_popover_content(self):
        mm = self.controller.mode_manager
        modes = mm.get_modes()

        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        container.set_margin_start(6)
        container.set_margin_end(6)
        container.set_margin_top(6)
        container.set_margin_bottom(6)

        list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        list_box.add_css_class("boxed-list")
        list_box.set_size_request(300, -1)

        for name, mode in modes.items():
            list_box.append(self._build_mode_row(name, mode))

        container.append(list_box)

        # Footer: create a new mode.
        create_row = Gtk.Button(
            label=_("Create a new Mode"),
            css_classes=["flat"],
        )
        create_row.connect("clicked", self._on_create_clicked)
        container.append(create_row)

        return container

    def _build_mode_row(self, name: str, mode: dict) -> Gtk.ListBoxRow:
        row = Adw.ActionRow(
            title=name,
            subtitle=mode.get("description") or "",
            activatable=True,
        )
        icon_name = mode.get("icon") or DEFAULT_MODE_ICON
        row.add_prefix(Gtk.Image(icon_name=icon_name))

        # Highlight the active mode.
        if name == self.controller.mode_manager.get_active_mode_name():
            row.add_css_class("suggested-action")

        # Edit button on the right.
        edit_btn = Gtk.Button(
            icon_name="document-edit-symbolic",
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Edit Mode"),
        )
        edit_btn.connect("clicked", lambda _b, n=name: self._on_edit_clicked(n))
        row.add_suffix(edit_btn)

        row.connect("activated", lambda _r, n=name: self._on_mode_activated(n))
        return row

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def refresh(self):
        """Rebuild the popover and update the button label to the active mode."""
        mm = self.controller.mode_manager
        active = mm.get_active_mode()
        icon_name = active.get("icon") or DEFAULT_MODE_ICON
        self.icon.set_from_icon_name(icon_name)
        self.name_label.set_label(mm.get_active_mode_name())
        self.popover.set_child(self._build_popover_content())

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #
    def _on_mode_activated(self, name: str):
        mm = self.controller.mode_manager
        if name == mm.get_active_mode_name():
            self.popover.popdown()
            return
        mm.set_active_mode(name)
        # Propagate skill overrides to the skill manager.
        active = mm.get_active_mode()
        self.controller.skill_manager.set_mode_overrides(active.get("skills", {}))
        # Reload prompt and tool overrides for the newly active mode.
        self.controller.update_settings()
        self.refresh()
        self.popover.popdown()
        self.emit("mode-changed")

    def _on_edit_clicked(self, name: str):
        self._open_editor(name)

    def _on_create_clicked(self, _button):
        self._open_editor(None)

    def _open_editor(self, name):
        # Imported lazily to avoid a circular import (mode_editor imports
        # nothing from here, but it imports other widgets).
        from .mode_editor import ModeEditorDialog

        self.popover.popdown()
        dialog = ModeEditorDialog(self.controller, self.window, mode_name=name)
        dialog.present()
