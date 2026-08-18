"""Skill Editor canvas integration and live editing context."""

from __future__ import annotations

import gettext
import json
import threading
import weakref

from gi.repository import Gio, GLib

from ..extensions import NewelleExtension
from ..handlers import TabButtonDescription

_ = gettext.gettext


class SkillEditorIntegration(NewelleExtension):
    id = "skill-editor"
    name = "Skill Editor"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)
        self._editor_states = {}
        self._editor_states_lock = threading.Lock()
        self._editor_state_sequence = 0

    @property
    def controller(self):
        return self.ui_controller.window.controller

    def add_tab_menu_entries(self) -> list:
        return [
            TabButtonDescription(
                _("Skill Editor"),
                "skills-symbolic",
                self._open_skill_editor,
            )
        ]

    def _open_skill_editor(self, _tabview=None, _file=None):
        from ..ui.widgets.skill_editor_mini_app import SkillEditorMiniApp

        mini_app = SkillEditorMiniApp(
            self.controller,
            on_context_changed=self._update_editor_state,
        )
        tab = self.ui_controller.add_tab(mini_app)
        tab.set_title(_("Skill Editor"))
        tab.set_icon(Gio.ThemedIcon(name="skills-symbolic"))
        tab.connect(
            "notify::selected",
            self._on_editor_tab_selected,
            weakref.ref(mini_app),
        )
        mini_app.sync_context_state()
        return tab

    def _on_editor_tab_selected(self, _tab, _pspec, editor_ref):
        editor = editor_ref()
        if editor is not None:
            GLib.idle_add(editor.sync_context_state)

    def _forget_editor(self, editor_id):
        with self._editor_states_lock:
            self._editor_states.pop(editor_id, None)

    def _update_editor_state(
        self,
        editor,
        snapshot,
        rooted,
        context_enabled,
        mapped,
    ):
        """Cache UI-owned state so generation threads never touch GTK widgets."""
        editor_id = id(editor)
        with self._editor_states_lock:
            self._editor_state_sequence += 1
            state = self._editor_states.get(editor_id)
            if state is None:
                integration_ref = weakref.ref(self)

                def forget(_editor_ref, key=editor_id):
                    integration = integration_ref()
                    if integration is not None:
                        integration._forget_editor(key)

                state = {"editor_ref": weakref.ref(editor, forget)}
                self._editor_states[editor_id] = state
            state.update({
                "snapshot": snapshot,
                "rooted": rooted,
                "context_enabled": context_enabled,
                "mapped": mapped,
                "sequence": self._editor_state_sequence,
            })

    def _current_snapshot(self):
        with self._editor_states_lock:
            candidates = [
                state
                for state in self._editor_states.values()
                if state.get("rooted")
                and state.get("context_enabled")
                and state.get("snapshot") is not None
            ]
            if not candidates:
                return None
            visible = [state for state in candidates if state.get("mapped")]
            current = max(
                visible or candidates,
                key=lambda state: state["sequence"],
            )
            return dict(current["snapshot"])

    @staticmethod
    def _format_snapshot(snapshot):
        dirty = "yes" if snapshot["dirty"] else "no"
        preview = "shown" if snapshot["preview_visible"] else "hidden"
        context = [
            (
                "The user currently has Newelle's Skill Editor open. The following "
                "is a transient snapshot of the active editor, including unsaved "
                "changes."
            ),
            f"Skill folder: {json.dumps(snapshot['path'], ensure_ascii=False)}",
            f"Unsaved changes: {dirty}",
            f"Validation status: {snapshot['validation']}",
            "Cursor: line {}, column {}".format(
                snapshot["cursor_line"],
                snapshot["cursor_column"],
            ),
            f"Markdown preview: {preview}",
        ]
        if snapshot["selection"]:
            context.append(
                "Selected text: "
                + json.dumps(snapshot["selection"], ensure_ascii=False)
            )
        context.extend((
            "Current live SKILL.md:",
            "<skill-editor-document>",
            snapshot["document"],
            "</skill-editor-document>",
            (
                "Use this as the current skill workspace when the user refers to "
                "the skill, the editor, the selection, or unsaved changes."
            ),
            (
                "Treat this editor-owned draft as read-only context. Only suggest "
                "what the user should write or change in the editor; do not create, "
                "modify, overwrite, rename, or delete SKILL.md or any other file in "
                "the skill folder, and do not use filesystem or terminal actions to "
                "apply those suggestions. The user remains responsible for applying "
                "and saving edits through Skill Editor."
            ),
        ))
        return "\n".join(context)

    def preprocess_history(self, history: list, prompts: list) -> tuple[list, list]:
        snapshot = self._current_snapshot()
        if snapshot is None:
            return history, prompts
        prompts.append(self._format_snapshot(snapshot))
        return history, prompts
