"""Structured editor and independent window for local Agent Skills."""

from __future__ import annotations

import gettext
import json
import os
import re
import shutil
import stat
import tempfile
import weakref
from urllib.parse import urlparse

from gi.repository import Adw, GLib, Gtk, GtkSource, Pango

from ..skills import parse_frontmatter
from ..utility.strings import markwon_to_pango
from ..utility.system import open_folder, open_website

_ = gettext.gettext


SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_COMPATIBILITY_LENGTH = 500
MAX_INSTRUCTIONS_BYTES = 512 * 1024
PREVIEW_DEBOUNCE_MS = 180


STARTER_INSTRUCTIONS = """# Purpose

Explain what this skill helps the agent accomplish and the outcome it should produce.

## Workflow

1. Inspect the user's request and any relevant files.
2. Follow the domain-specific steps for this skill.
3. Verify the result before returning it.

## Output

Describe the expected response or artifact format. Add concise examples when they make the workflow clearer.
"""


class SkillCreationError(ValueError):
    """Raised when a local skill draft cannot be created safely."""


def validate_skill_name(name):
    """Return a normalized folder-safe skill identifier."""
    name = name.strip()
    if not name:
        raise SkillCreationError(_("Add a skill name"))
    if len(name) > MAX_NAME_LENGTH or not SKILL_NAME_RE.fullmatch(name):
        raise SkillCreationError(
            _("Use lowercase letters, numbers, and single hyphens for the name")
        )
    return name


def validate_skill_draft(name, description, compatibility, instructions):
    """Validate and normalize the structured fields used by ``SKILL.md``."""
    name = validate_skill_name(name)
    description = description.strip()
    compatibility = compatibility.strip()
    instructions = instructions.strip()

    if not description:
        raise SkillCreationError(
            _("Describe what the skill does and when to use it")
        )
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise SkillCreationError(_("The description is too long"))
    if len(compatibility) > MAX_COMPATIBILITY_LENGTH:
        raise SkillCreationError(_("The compatibility note is too long"))
    if not instructions:
        raise SkillCreationError(_("Add instructions for the skill"))
    if len(instructions.encode("utf-8")) > MAX_INSTRUCTIONS_BYTES:
        raise SkillCreationError(_("The instructions are too large"))

    return {
        "name": name,
        "description": description,
        "compatibility": compatibility,
        "instructions": instructions,
    }


def build_skill_document(name, description, compatibility, instructions):
    """Build a portable SKILL.md document from validated structured fields."""
    draft = validate_skill_draft(name, description, compatibility, instructions)
    metadata = [
        "---",
        f"name: {json.dumps(draft['name'], ensure_ascii=False)}",
        f"description: {json.dumps(draft['description'], ensure_ascii=False)}",
    ]
    if draft["compatibility"]:
        metadata.append(
            "compatibility: "
            + json.dumps(draft["compatibility"], ensure_ascii=False)
        )
    metadata.extend(("---", ""))
    return "\n".join(metadata) + draft["instructions"] + "\n"


def _skills_root(skills_dir):
    skills_dir = os.path.abspath(skills_dir)
    os.makedirs(skills_dir, exist_ok=True)
    if not os.path.isdir(skills_dir):
        raise SkillCreationError(_("The skills location is not a folder"))
    return skills_dir


def create_skill_folder(skills_dir, name):
    """Create an empty skill folder so resources can be added before saving."""
    skills_dir = _skills_root(skills_dir)
    name = validate_skill_name(name)
    destination = os.path.join(skills_dir, name)
    try:
        os.mkdir(destination)
    except FileExistsError as error:
        raise SkillCreationError(
            _("A skill folder with this name already exists")
        ) from error
    return destination


def _write_skill_document(
    skill_dir,
    name,
    description,
    compatibility,
    instructions,
    *,
    overwrite,
):
    """Atomically write SKILL.md while preserving an existing file's mode."""
    document = build_skill_document(name, description, compatibility, instructions)
    skill_dir = os.path.abspath(skill_dir)
    if not os.path.isdir(skill_dir):
        raise SkillCreationError(_("The skill folder no longer exists"))

    skill_path = os.path.join(skill_dir, "SKILL.md")
    exists = os.path.isfile(skill_path)
    if overwrite and not exists:
        raise SkillCreationError(_("SKILL.md no longer exists"))
    if not overwrite and os.path.lexists(skill_path):
        raise SkillCreationError(_("This folder already contains SKILL.md"))

    file_mode = stat.S_IMODE(os.stat(skill_path).st_mode) if exists else 0o644
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".SKILL.md-",
        dir=skill_dir,
    )
    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            os.fchmod(handle.fileno(), file_mode)
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, skill_path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise
    return skill_dir


def save_skill_document(
    skill_dir,
    name,
    description,
    compatibility,
    instructions,
    *,
    overwrite=False,
):
    """Save a skill into a prepared folder, optionally replacing SKILL.md."""
    return _write_skill_document(
        skill_dir,
        name,
        description,
        compatibility,
        instructions,
        overwrite=overwrite,
    )


def create_skill_files(skills_dir, name, description, compatibility, instructions):
    """Atomically create one complete skill directory."""
    draft = validate_skill_draft(name, description, compatibility, instructions)
    skills_dir = _skills_root(skills_dir)
    destination = os.path.join(skills_dir, draft["name"])
    if os.path.lexists(destination):
        raise SkillCreationError(_("A skill folder with this name already exists"))

    temporary_dir = tempfile.mkdtemp(
        prefix=f".{draft['name']}-",
        dir=skills_dir,
    )
    try:
        _write_skill_document(temporary_dir, **draft, overwrite=False)
        os.rename(temporary_dir, destination)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return destination


def resolve_editor_style_scheme(manager, requested, dark):
    """Resolve the configured GtkSource scheme with an app-themed fallback."""
    fallbacks = (
        "Adwaita-dark" if dark else "Adwaita",
        "classic-dark" if dark else "classic",
        "Adwaita",
    )
    for scheme_id in (requested, *fallbacks):
        if scheme_id:
            scheme = manager.get_scheme(scheme_id)
            if scheme is not None:
                return scheme
    return None


def _disconnect_theme_signals(settings, settings_handler, style, style_handler):
    for owner, handler in (
        (settings, settings_handler),
        (style, style_handler),
    ):
        if owner is None or handler is None:
            continue
        try:
            owner.disconnect(handler)
        except (TypeError, AttributeError):
            pass


class SkillCreatorView(Gtk.Box):
    """Native skill authoring form with source editing and live preview."""

    def __init__(
        self,
        host,
        controller,
        on_saved=None,
        on_open_window=None,
        on_path_changed=None,
        on_context_changed=None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.host = host
        self.controller = controller
        self.on_saved = on_saved
        self.on_open_window = on_open_window
        self.on_path_changed = on_path_changed
        self.on_context_changed = on_context_changed
        self._preview_source_id = None
        self._editing_path = None
        self._draft_folder_path = None
        self._last_created_path = None
        self._saved_snapshot = None
        self._suspend_changes = False

        self._build_details()
        self._build_editor()
        self._build_destination()
        self._reset_draft()

    def set_host(self, host):
        self.host = host

    def set_windowed(self, windowed):
        self.open_window_button.set_visible(not windowed)

    def set_open_window_callback(self, callback):
        self.on_open_window = callback

    def get_context_path(self):
        """Return the concrete or expected skill folder represented by the draft."""
        for path in (
            self._editing_path,
            self._draft_folder_path,
            self._last_created_path,
        ):
            if path:
                return os.path.abspath(path)
        try:
            name = validate_skill_name(self.name_row.get_text())
        except SkillCreationError:
            return os.path.abspath(self.controller.skills_path)
        return os.path.abspath(os.path.join(self.controller.skills_path, name))

    def _notify_path_changed(self):
        if self.on_path_changed is not None:
            self.on_path_changed(self.get_context_path())

    def _notify_context_changed(self):
        if self.on_context_changed is not None:
            self.on_context_changed()

    def get_context_snapshot(self):
        """Return the live, potentially unsaved editor state as plain data."""
        instructions = self._instructions()
        metadata = [
            "---",
            f"name: {json.dumps(self.name_row.get_text(), ensure_ascii=False)}",
            "description: "
            + json.dumps(self.description_row.get_text(), ensure_ascii=False),
        ]
        compatibility = self.compatibility_row.get_text()
        if compatibility:
            metadata.append(
                "compatibility: " + json.dumps(compatibility, ensure_ascii=False)
            )
        metadata.extend(("---", ""))
        document = "\n".join(metadata) + instructions

        selection_text = ""
        selection = self.editor_buffer.get_selection_bounds()
        if selection:
            selection_start, selection_end = selection[-2:]
            selection_text = self.editor_buffer.get_text(
                selection_start,
                selection_end,
                True,
            )

        cursor = self.editor_buffer.get_iter_at_mark(
            self.editor_buffer.get_insert()
        )
        return {
            "path": self.get_context_path(),
            "name": self.name_row.get_text(),
            "description": self.description_row.get_text(),
            "compatibility": compatibility,
            "document": document,
            "dirty": self.has_unsaved_changes(),
            "validation": self.validation_label.get_text(),
            "cursor_line": cursor.get_line() + 1,
            "cursor_column": cursor.get_line_offset() + 1,
            "selection": selection_text,
            "preview_visible": self.preview_toggle.get_active(),
        }

    def _toast(self, title):
        if self.host is not None and hasattr(self.host, "add_toast"):
            self.host.add_toast(Adw.Toast(title=title))

    def _build_details(self):
        details_group = Adw.PreferencesGroup(
            title=_("Skill details"),
            description=_(
                "The description controls when the agent discovers this skill. "
                "Include what it does and when it should be used."
            ),
        )
        self.name_row = Adw.EntryRow(title=_("Name"))
        self.name_row.set_max_length(MAX_NAME_LENGTH)
        self.name_row.set_tooltip_text(
            _("A lowercase identifier such as release-notes")
        )
        self.description_row = Adw.EntryRow(title=_("Description"))
        self.description_row.set_max_length(MAX_DESCRIPTION_LENGTH)
        self.description_row.set_tooltip_text(
            _("What the skill does and the situations that should trigger it")
        )
        self.compatibility_row = Adw.EntryRow(title=_("Compatibility (optional)"))
        self.compatibility_row.set_max_length(MAX_COMPATIBILITY_LENGTH)
        self.compatibility_row.set_tooltip_text(
            _("Required tools, platforms, or dependencies")
        )
        for row in (
            self.name_row,
            self.description_row,
            self.compatibility_row,
        ):
            row.connect("changed", self._on_draft_changed)
            details_group.add(row)
        self.append(details_group)

    def _build_editor(self):
        instructions_group = Adw.PreferencesGroup(
            title=_("Instructions"),
            description=_(
                "Write the workflow in Markdown. Keep essential guidance here and "
                "place larger supporting files in the skill folder."
            ),
        )

        editor_card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            css_classes=["card"],
            overflow=Gtk.Overflow.HIDDEN,
        )
        editor_card.append(self._build_editor_header())
        editor_card.append(Gtk.Separator())

        self.editor_split = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        self.editor_split.set_wide_handle(True)
        self.editor_split.set_shrink_start_child(False)
        self.editor_split.set_shrink_end_child(False)
        self.editor_split.set_position(330)
        self.editor_split.set_start_child(self._build_source_panel())
        self.preview_panel = self._build_preview_panel()
        self.editor_split.set_end_child(self.preview_panel)
        editor_card.append(self.editor_split)

        instructions_group.add(editor_card)
        self.append(instructions_group)

    def _build_editor_header(self):
        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_start=12,
            margin_end=8,
            margin_top=6,
            margin_bottom=6,
        )
        title = Gtk.Label(
            label=_("SKILL.md body"),
            xalign=0,
            hexpand=True,
            css_classes=["heading"],
        )
        badge = Gtk.Label(
            label=_("Markdown"),
            css_classes=["caption", "dim-label"],
        )
        self.open_window_button = Gtk.Button(
            icon_name="detach-symbolic",
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
        )
        self.open_window_button.set_tooltip_text(_("Open editor in a new window"))
        self.open_window_button.connect("clicked", self._on_open_window)
        self.preview_toggle = Gtk.ToggleButton(
            icon_name="view-show-symbolic",
            active=True,
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
        )
        self.preview_toggle.set_tooltip_text(_("Hide Markdown preview"))
        self.preview_toggle.connect("toggled", self._on_preview_toggled)
        header.append(title)
        header.append(badge)
        header.append(self.open_window_button)
        header.append(self.preview_toggle)
        return header

    def _build_source_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.append(
            Gtk.Label(
                label=_("EDIT"),
                xalign=0,
                margin_start=12,
                margin_top=8,
                margin_bottom=6,
                css_classes=["caption", "dim-label"],
            )
        )
        self.editor_buffer = GtkSource.Buffer()
        language = GtkSource.LanguageManager.get_default().get_language("markdown")
        if language is not None:
            self.editor_buffer.set_language(language)
        self.editor_buffer.set_highlight_syntax(True)
        self.editor_buffer.connect("changed", self._on_instructions_changed)
        self.editor_buffer.connect("mark-set", self._on_editor_mark_set)
        self._setup_editor_theme()

        self.editor_view = GtkSource.View(buffer=self.editor_buffer)
        self.editor_view.set_monospace(True)
        self.editor_view.set_show_line_numbers(True)
        self.editor_view.set_highlight_current_line(True)
        self.editor_view.set_auto_indent(True)
        self.editor_view.set_insert_spaces_instead_of_tabs(True)
        self.editor_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.editor_view.set_top_margin(10)
        self.editor_view.set_bottom_margin(10)
        self.editor_view.set_left_margin(10)
        self.editor_view.set_right_margin(10)

        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=330,
            hexpand=True,
            vexpand=True,
        )
        scroller.set_child(self.editor_view)
        panel.append(scroller)
        return panel

    def _setup_editor_theme(self):
        self._style_scheme_manager = GtkSource.StyleSchemeManager.new()
        self._app_style_manager = Adw.StyleManager.get_default()
        settings = getattr(self.controller, "settings", None)
        editor_ref = weakref.ref(self)

        def update_theme(*_args):
            editor = editor_ref()
            if editor is not None:
                editor._apply_editor_theme()

        settings_handler = None
        if settings is not None:
            settings_handler = settings.connect(
                "changed::editor-color-scheme",
                update_theme,
            )
        style_handler = self._app_style_manager.connect(
            "notify::dark",
            update_theme,
        )
        weakref.finalize(
            self,
            _disconnect_theme_signals,
            settings,
            settings_handler,
            self._app_style_manager,
            style_handler,
        )
        self._apply_editor_theme()

    def _apply_editor_theme(self):
        settings = getattr(self.controller, "settings", None)
        requested = ""
        if settings is not None:
            try:
                requested = settings.get_string("editor-color-scheme")
            except (AttributeError, TypeError):
                pass
        if not requested:
            requested = getattr(
                getattr(self.controller, "newelle_settings", None),
                "editor_color_scheme",
                "",
            )
        scheme = resolve_editor_style_scheme(
            self._style_scheme_manager,
            requested,
            self._app_style_manager.get_dark(),
        )
        self.editor_buffer.set_style_scheme(scheme)

    def _build_preview_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.append(
            Gtk.Label(
                label=_("PREVIEW"),
                xalign=0,
                margin_start=12,
                margin_top=8,
                margin_bottom=6,
                css_classes=["caption", "dim-label"],
            )
        )
        self.preview_label = Gtk.Label(
            xalign=0,
            yalign=0,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
            margin_start=16,
            margin_end=16,
            margin_top=10,
            margin_bottom=16,
        )
        self.preview_label.connect("activate-link", self._on_preview_link)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=330,
            hexpand=True,
            vexpand=True,
        )
        scroller.set_child(self.preview_label)
        panel.append(scroller)
        return panel

    def _build_destination(self):
        self.destination_group = Adw.PreferencesGroup(
            title=_("Create skill"),
            description=_(
                "Create the folder early to add scripts, references, and assets "
                "while you continue editing."
            ),
        )
        self.destination_row = Adw.ActionRow(
            title=_("Skill folder"),
            subtitle=self.controller.skills_path,
        )
        self.open_folder_button = Gtk.Button(
            label=_("Open Folder"),
            icon_name="folder-visiting-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        self.open_folder_button.connect("clicked", self._on_open_folder)
        self.destination_row.add_suffix(self.open_folder_button)
        self.destination_group.add(self.destination_row)

        action_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=12,
        )
        self.validation_label = Gtk.Label(
            xalign=0,
            hexpand=True,
            wrap=True,
            css_classes=["caption", "dim-label"],
        )
        self.new_draft_button = Gtk.Button(
            label=_("New Draft"),
            icon_name="document-new-symbolic",
            visible=False,
        )
        self.new_draft_button.connect("clicked", self._on_new_draft)
        self.create_folder_button = Gtk.Button(
            label=_("Create Folder"),
            icon_name="folder-new-symbolic",
        )
        self.create_folder_button.connect("clicked", self._on_create_folder)
        self.save_button = Gtk.Button(
            label=_("Create Skill"),
            icon_name="document-save-symbolic",
            css_classes=["suggested-action"],
        )
        self.save_button.connect("clicked", self._on_save)
        action_box.append(self.validation_label)
        action_box.append(self.new_draft_button)
        action_box.append(self.create_folder_button)
        action_box.append(self.save_button)
        self.destination_group.add(action_box)
        self.append(self.destination_group)

    def _instructions(self):
        start = self.editor_buffer.get_start_iter()
        end = self.editor_buffer.get_end_iter()
        return self.editor_buffer.get_text(start, end, True)

    def _draft(self):
        return (
            self.name_row.get_text(),
            self.description_row.get_text(),
            self.compatibility_row.get_text(),
            self._instructions(),
        )

    def _snapshot(self):
        draft = validate_skill_draft(*self._draft())
        return tuple(draft[key] for key in (
            "name",
            "description",
            "compatibility",
            "instructions",
        ))

    def _set_draft_fields(self, name, description, compatibility, instructions):
        self._suspend_changes = True
        try:
            self.name_row.set_text(name)
            self.description_row.set_text(description)
            self.compatibility_row.set_text(compatibility)
            self.editor_buffer.set_text(instructions)
        finally:
            self._suspend_changes = False
        if self._preview_source_id is not None:
            GLib.source_remove(self._preview_source_id)
            self._preview_source_id = None
        self._render_preview()

    def _reset_draft(self):
        self._set_draft_fields("", "", "", STARTER_INSTRUCTIONS)
        self._editing_path = None
        self._draft_folder_path = None
        self._last_created_path = None
        self._saved_snapshot = None
        self.name_row.set_sensitive(True)
        self.destination_group.set_title(_("Create skill"))
        self.destination_row.set_subtitle(self.controller.skills_path)
        self.new_draft_button.set_visible(False)
        self.save_button.set_label(_("Create Skill"))
        self._update_form_state()
        self._notify_path_changed()
        self._notify_context_changed()

    def load_skill(self, skill):
        """Load an installed skill for editing without copying its resources."""
        try:
            with open(skill.location, "r", encoding="utf-8") as handle:
                metadata, instructions = parse_frontmatter(handle.read())
        except (OSError, UnicodeDecodeError) as error:
            self._toast(_("Could not open skill: {}").format(error))
            return False

        self._editing_path = os.path.abspath(skill.base_dir)
        self._draft_folder_path = None
        self._last_created_path = self._editing_path
        self._set_draft_fields(
            metadata.get("name") or skill.name,
            metadata.get("description") or skill.description,
            metadata.get("compatibility", ""),
            instructions,
        )
        try:
            self._saved_snapshot = self._snapshot()
        except SkillCreationError:
            self._saved_snapshot = None
        self.name_row.set_sensitive(True)
        self.destination_group.set_title(_("Edit skill"))
        self.destination_row.set_subtitle(self._editing_path)
        self.new_draft_button.set_visible(True)
        self._update_form_state()
        self._notify_path_changed()
        self._notify_context_changed()
        return True

    def has_unsaved_changes(self):
        if self._editing_path is not None:
            try:
                return self._snapshot() != self._saved_snapshot
            except SkillCreationError:
                return True
        if self._draft_folder_path is not None:
            return True
        return any((
            self.name_row.get_text().strip(),
            self.description_row.get_text().strip(),
            self.compatibility_row.get_text().strip(),
            self._instructions().strip() != STARTER_INSTRUCTIONS.strip(),
        ))

    def new_draft(self):
        """Start a fresh draft while leaving any prepared folder on disk."""
        kept_folder = self._draft_folder_path
        self._reset_draft()
        self.name_row.grab_focus()
        if kept_folder and os.path.isdir(kept_folder):
            self._toast(_("Draft folder kept at {}").format(kept_folder))

    def _on_new_draft(self, _button):
        self.new_draft()

    def _on_draft_changed(self, _row):
        if self._suspend_changes:
            return
        self._update_form_state()
        self._notify_path_changed()
        self._notify_context_changed()

    def _on_instructions_changed(self, _buffer):
        if self._suspend_changes:
            return
        self._update_form_state()
        self._notify_context_changed()
        if self._preview_source_id is not None:
            GLib.source_remove(self._preview_source_id)
        self._preview_source_id = GLib.timeout_add(
            PREVIEW_DEBOUNCE_MS,
            self._render_preview,
        )

    def _on_editor_mark_set(self, _buffer, _location, mark):
        if self._suspend_changes:
            return
        if mark in (
            self.editor_buffer.get_insert(),
            self.editor_buffer.get_selection_bound(),
        ):
            self._notify_context_changed()

    def _update_folder_button(self):
        creating = self._editing_path is None and self._draft_folder_path is None
        self.create_folder_button.set_visible(creating)
        if not creating:
            return
        try:
            name = validate_skill_name(self.name_row.get_text())
            destination = os.path.join(self.controller.skills_path, name)
            available = (
                not os.path.lexists(destination)
                and not self._has_name_conflict(name)
            )
        except SkillCreationError:
            available = False
        self.create_folder_button.set_sensitive(available)

    def _has_name_conflict(self, name):
        for skill in self.controller.skill_manager.skills.values():
            if skill.name != name:
                continue
            if self._editing_path is None:
                return True
            if os.path.abspath(skill.base_dir) != self._editing_path:
                return True
        return False

    def _update_form_state(self):
        self._update_folder_button()
        try:
            draft = validate_skill_draft(*self._draft())
            if self._has_name_conflict(draft["name"]):
                raise SkillCreationError(_("Another installed skill uses this name"))

            if self._editing_path is not None:
                if not os.path.isfile(os.path.join(self._editing_path, "SKILL.md")):
                    raise SkillCreationError(_("SKILL.md no longer exists"))
            elif self._draft_folder_path is not None:
                if not os.path.isdir(self._draft_folder_path):
                    raise SkillCreationError(_("The draft folder no longer exists"))
                if os.path.lexists(
                    os.path.join(self._draft_folder_path, "SKILL.md")
                ):
                    raise SkillCreationError(
                        _("The draft folder already contains SKILL.md")
                    )
            else:
                destination = os.path.join(
                    self.controller.skills_path,
                    draft["name"],
                )
                if os.path.lexists(destination):
                    raise SkillCreationError(
                        _("A skill folder with this name already exists")
                    )
        except SkillCreationError as error:
            self.save_button.set_sensitive(False)
            self.validation_label.set_text(str(error))
            return

        if self._editing_path is not None:
            self.save_button.set_label(_("Save Changes"))
            try:
                unchanged = self._snapshot() == self._saved_snapshot
            except SkillCreationError:
                unchanged = False
            self.save_button.set_sensitive(not unchanged)
            self.validation_label.set_text(
                _("No unsaved changes")
                if unchanged
                else _("Ready to update SKILL.md")
            )
        else:
            self.save_button.set_label(_("Create Skill"))
            self.save_button.set_sensitive(True)
            folder = self._draft_folder_path or os.path.join(
                self.controller.skills_path,
                draft["name"],
            )
            self.validation_label.set_text(
                _("Ready to create {}/SKILL.md").format(
                    os.path.basename(folder)
                )
            )

    def _render_preview(self):
        self._preview_source_id = None
        instructions = self._instructions().strip()
        if not instructions:
            self.preview_label.set_markup(
                '<span alpha="55%">' + _("Nothing to preview yet") + "</span>"
            )
            return GLib.SOURCE_REMOVE
        self.preview_label.set_markup(markwon_to_pango(instructions))
        return GLib.SOURCE_REMOVE

    def _on_preview_link(self, _label, uri):
        parsed = urlparse(uri)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            open_website(uri)
        return True

    def _on_preview_toggled(self, button):
        visible = button.get_active()
        self.preview_panel.set_visible(visible)
        button.set_icon_name(
            "view-show-symbolic" if visible else "view-conceal-symbolic"
        )
        button.set_tooltip_text(
            _("Hide Markdown preview")
            if visible
            else _("Show Markdown preview")
        )
        self._notify_context_changed()

    def _on_open_window(self, _button):
        if self.on_open_window is not None:
            self.on_open_window(self)

    def _folder_target(self):
        for path in (
            self._editing_path,
            self._draft_folder_path,
            self._last_created_path,
        ):
            if path and os.path.isdir(path):
                return path
        return self.controller.skills_path

    def _on_open_folder(self, _button):
        os.makedirs(self.controller.skills_path, exist_ok=True)
        open_folder(self._folder_target())

    def _on_create_folder(self, _button):
        try:
            destination = create_skill_folder(
                self.controller.skills_path,
                self.name_row.get_text(),
            )
        except (OSError, SkillCreationError) as error:
            self._toast(_("Could not create folder: {}").format(error))
            self._update_form_state()
            return

        self._draft_folder_path = destination
        self._last_created_path = destination
        self.name_row.set_sensitive(False)
        self.destination_row.set_subtitle(destination)
        self.new_draft_button.set_visible(True)
        self._update_form_state()
        self._notify_path_changed()
        self._notify_context_changed()
        self._toast(_("Skill folder created. You can keep editing."))

    def _on_save(self, _button):
        try:
            if self._editing_path is not None:
                destination = save_skill_document(
                    self._editing_path,
                    *self._draft(),
                    overwrite=True,
                )
                toast_title = _("Skill '{}' updated")
            elif self._draft_folder_path is not None:
                destination = save_skill_document(
                    self._draft_folder_path,
                    *self._draft(),
                )
                toast_title = _("Skill '{}' created")
            else:
                destination = create_skill_files(
                    self.controller.skills_path,
                    *self._draft(),
                )
                toast_title = _("Skill '{}' created")
            self.controller.skill_manager.discover()
        except (OSError, SkillCreationError) as error:
            self._toast(_("Could not save skill: {}").format(error))
            self._update_form_state()
            return

        self._editing_path = destination
        self._draft_folder_path = None
        self._last_created_path = destination
        self._saved_snapshot = self._snapshot()
        self.name_row.set_sensitive(True)
        self.destination_group.set_title(_("Edit skill"))
        self.destination_row.set_subtitle(destination)
        self.new_draft_button.set_visible(True)
        self._update_form_state()
        self._notify_path_changed()
        self._notify_context_changed()
        if self.on_saved is not None:
            self.on_saved()
        self._toast(toast_title.format(self.name_row.get_text().strip()))


class SkillEditorWindow(Adw.ApplicationWindow):
    """Application-owned editor window that can outlive the Settings window."""

    def __init__(
        self,
        application,
        editor,
        return_editor=None,
        on_closed=None,
        title=None,
    ):
        super().__init__(
            application=application,
            title=title or _("Skill Editor"),
            default_width=980,
            default_height=780,
        )
        self.editor = editor
        self.return_editor = return_editor
        self.on_closed = on_closed
        self._closed_notified = False
        self.toast_overlay = Adw.ToastOverlay()

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        self.editor_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.editor_holder.append(editor)
        clamp = Adw.Clamp(
            maximum_size=1050,
            tightening_threshold=700,
            margin_start=18,
            margin_end=18,
            margin_top=18,
            margin_bottom=24,
        )
        clamp.set_child(self.editor_holder)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        scroller.set_child(clamp)
        toolbar_view.set_content(scroller)
        self.toast_overlay.set_child(toolbar_view)
        self.set_content(self.toast_overlay)

        editor.set_host(self)
        editor.set_windowed(True)
        self.connect("close-request", self._on_close_request)

    def add_toast(self, toast):
        self.toast_overlay.add_toast(toast)

    def _on_close_request(self, _window):
        if self.return_editor is not None and self.editor is not None:
            editor = self.editor
            self.editor_holder.remove(editor)
            if self.return_editor(editor):
                self.editor = None
        if not self._closed_notified and self.on_closed is not None:
            self._closed_notified = True
            self.on_closed()
        return False
