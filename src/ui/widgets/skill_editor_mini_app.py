"""Canvas mini app that reuses Newelle's structured Skill editor."""

from __future__ import annotations

import gettext
import os

from gi.repository import Adw, GLib, Gtk

from ..skill_creator import SkillCreatorView

_ = gettext.gettext
CONTEXT_DEBOUNCE_MS = 100


class SkillEditorMiniApp(Gtk.Box):
    """Create or edit Agent Skills inside a detachable canvas tab."""

    def __init__(self, controller, on_context_changed=None, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.controller = controller
        self.on_context_changed = on_context_changed
        self._skills = []
        self._context_source_id = None

        self.toast_overlay = Adw.ToastOverlay()
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        clamp = Adw.Clamp(
            maximum_size=1050,
            tightening_threshold=700,
            margin_start=18,
            margin_end=18,
            margin_top=18,
            margin_bottom=24,
        )
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        self.workspace_group = Adw.PreferencesGroup(
            title=_("Skill workspace"),
            description=_("Start a new skill or open an installed skill for editing."),
        )
        self.workspace_row = Adw.ActionRow(
            title=_("Open skill"),
            subtitle=self.controller.skills_path,
        )
        self.skill_dropdown = Gtk.DropDown(valign=Gtk.Align.CENTER)
        self.open_button = Gtk.Button(
            label=_("Open"),
            icon_name="document-open-symbolic",
            valign=Gtk.Align.CENTER,
        )
        self.open_button.connect("clicked", self._on_open_selected)
        self.workspace_row.add_suffix(self.skill_dropdown)
        self.workspace_row.add_suffix(self.open_button)
        self.workspace_group.add(self.workspace_row)

        self.context_row = Adw.ActionRow(
            title=_("Include editor in context"),
            subtitle=_(
                "Share the live, unsaved SKILL.md only while this editor is open."
            ),
        )
        self.context_switch = Gtk.Switch(
            active=True,
            valign=Gtk.Align.CENTER,
        )
        self.context_switch.set_tooltip_text(
            _("Allow the assistant to see this editor's current contents")
        )
        self.context_switch.connect("notify::active", self._on_context_toggled)
        self.context_row.add_suffix(self.context_switch)
        self.context_row.set_activatable_widget(self.context_switch)
        self.workspace_group.add(self.context_row)
        content.append(self.workspace_group)

        self.editor = SkillCreatorView(
            host=self,
            controller=self.controller,
            on_saved=self._on_saved,
            on_path_changed=self._on_path_changed,
            on_context_changed=self._on_editor_context_changed,
        )
        # Canvas tabs already have a native detach action in their header.
        self.editor.set_windowed(True)
        content.append(self.editor)

        self._refresh_skill_choices()
        clamp.set_child(content)
        scroller.set_child(clamp)
        self.toast_overlay.set_child(scroller)
        self.append(self.toast_overlay)
        self.connect("notify::root", self._on_context_lifecycle_changed)
        self._emit_context_state()

    def add_toast(self, toast):
        self.toast_overlay.add_toast(toast)

    def get_context_path(self):
        return self.editor.get_context_path()

    def get_context_snapshot(self):
        return self.editor.get_context_snapshot()

    def is_context_enabled(self):
        return self.context_switch.get_active()

    def sync_context_state(self):
        self._emit_context_state()

    def _cancel_context_state_update(self):
        if self._context_source_id is not None:
            GLib.source_remove(self._context_source_id)
            self._context_source_id = None

    def _publish_context_state(self):
        if self.on_context_changed is None or not hasattr(self, "editor"):
            return GLib.SOURCE_REMOVE
        enabled = self.is_context_enabled()
        rooted = self.get_root() is not None
        snapshot = self.get_context_snapshot() if enabled and rooted else None
        self.on_context_changed(
            self,
            snapshot,
            rooted,
            enabled,
            self.get_mapped(),
        )
        return GLib.SOURCE_REMOVE

    def _emit_context_state(self):
        self._cancel_context_state_update()
        return self._publish_context_state()

    def _flush_context_state(self):
        self._context_source_id = None
        return self._publish_context_state()

    def _schedule_context_state(self):
        self._cancel_context_state_update()
        self._context_source_id = GLib.timeout_add(
            CONTEXT_DEBOUNCE_MS,
            self._flush_context_state,
        )

    def _on_editor_context_changed(self):
        if self.is_context_enabled():
            self._schedule_context_state()

    def _on_context_toggled(self, _switch, _pspec):
        self._emit_context_state()

    def _on_context_lifecycle_changed(self, _widget, _pspec):
        self._emit_context_state()

    def _on_path_changed(self, path):
        self.workspace_row.set_subtitle(path)

    def _refresh_skill_choices(self):
        selected_name = None
        selected = self.skill_dropdown.get_selected()
        if 0 < selected <= len(self._skills):
            selected_name = self._skills[selected - 1].name

        self._skills = sorted(
            self.controller.skill_manager.skills.values(),
            key=lambda skill: skill.name.casefold(),
        )
        labels = [_('New skill')] + [skill.name for skill in self._skills]
        self.skill_dropdown.set_model(Gtk.StringList.new(labels))
        if selected_name:
            for index, skill in enumerate(self._skills, start=1):
                if skill.name == selected_name:
                    self.skill_dropdown.set_selected(index)
                    break
        else:
            self.skill_dropdown.set_selected(0)

    def _on_saved(self):
        self._refresh_skill_choices()
        current_path = self.editor.get_context_path()
        for index, skill in enumerate(self._skills, start=1):
            if os.path.abspath(skill.base_dir) == current_path:
                self.skill_dropdown.set_selected(index)
                break

    def _on_open_selected(self, _button):
        if not self.editor.has_unsaved_changes():
            self._open_selected()
            return

        root = self.get_root()
        dialog = Adw.MessageDialog(
            transient_for=root if isinstance(root, Gtk.Window) else None,
            heading=_("Discard unsaved changes?"),
            body=_("Opening another skill replaces the current editor draft."),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("discard", _("Discard and Open"))
        dialog.set_response_appearance(
            "discard",
            Adw.ResponseAppearance.DESTRUCTIVE,
        )
        dialog.connect("response", self._on_open_confirmation)
        dialog.present()

    def _on_open_confirmation(self, dialog, response):
        if response == "discard":
            self._open_selected()
        dialog.destroy()

    def _open_selected(self):
        selected = self.skill_dropdown.get_selected()
        if selected == 0:
            self.editor.new_draft()
            return
        index = selected - 1
        if 0 <= index < len(self._skills):
            self.editor.load_skill(self._skills[index])
