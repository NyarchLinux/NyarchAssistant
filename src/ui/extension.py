import os
import subprocess
from threading import Thread

from gi.repository import Adw, GLib, Gtk

from ..controller import NewelleController
from ..extensions import ExtensionLoader
from ..utility.system import can_escape_sandbox, get_spawn_command
from .extra_settings import ExtraSettingsBuilder
from .widgets import CopyBox


class ExtensionPage(Adw.PreferencesPage):
    """Manage user-installed extensions inside the settings window."""

    def __init__(
        self,
        app,
        controller: NewelleController,
        toast_callback=None,
    ):
        super().__init__(
            icon_name="extension-symbolic",
            title=_("Extensions"),
        )
        self.app = app
        self.controller = controller
        self.settings = controller.settings
        self.toast_callback = toast_callback
        self.extension_path = controller.extension_path
        self.pip_directory = controller.pip_path
        self.extensions_cache = controller.extensions_cache
        self.sandbox = can_escape_sandbox()
        self.page_groups = []
        self.update()

    def _add_toast(self, title):
        if self.toast_callback is not None:
            self.toast_callback(Adw.Toast(title=title))

    def _parent_window(self):
        root = self.get_root()
        return root if isinstance(root, Gtk.Window) else None

    def _clear_page(self):
        for group in self.page_groups:
            self.remove(group)
        self.page_groups = []

    def _add_group(self, group):
        self.add(group)
        self.page_groups.append(group)

    def update(self):
        self._clear_page()
        self.extensionloader = self.controller.extensionloader
        self.extra_settings_rows = {}
        self.extra_settings_builder = ExtraSettingsBuilder(
            settingsrows=self.extra_settings_rows,
            convert_constants=self._convert_extension_constants,
        )

        self.extensiongroup = Adw.PreferencesGroup(title=_("Installed Extensions"))
        self._add_group(self.extensiongroup)

        for extension in self.extensionloader.get_extensions():
            self._add_extension_row(extension)

        self._add_extension_actions()

    def _add_extension_row(self, extension):
        self.extra_settings_rows[(extension.key, "extension", False)] = {}
        extension.set_extra_settings_update(
            lambda _, current_extension=extension: GLib.idle_add(
                self.extra_settings_builder.on_setting_change,
                self.extensionloader.extensionsmap,
                current_extension,
                current_extension.key,
                True,
            )
        )

        toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        toggle.set_active(extension not in self.extensionloader.disabled_extensions)
        toggle.connect("state-set", self.change_status, extension.id)

        has_extra_settings = bool(extension.get_extra_settings())
        if has_extra_settings:
            row = Adw.ExpanderRow(title=extension.name)
            row.add_suffix(toggle)
            self.extra_settings_builder.add_extra_settings(
                self.extensionloader.extensionsmap,
                extension,
                row,
            )
        else:
            row = Adw.ActionRow(title=extension.name)
            row.add_suffix(toggle)

        row.add_prefix(
            Gtk.Image(icon_name="extension-symbolic", css_classes=["dim-label"])
        )

        delete_button = Gtk.Button(
            icon_name="user-trash-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat", "destructive-action"],
        )
        delete_button.set_tooltip_text(_("Remove"))
        delete_button.connect("clicked", self.delete_extension, extension.id)
        row.add_suffix(delete_button)

        self.extra_settings_rows[(extension.key, "extension", False)]["row"] = row
        self.add_flatpak_warning_button(extension, row)
        self.extensiongroup.add(row)

    def _add_extension_actions(self):
        actions = Adw.PreferencesGroup()
        self._add_group(actions)

        guide_row = Adw.ActionRow(title=_("User guide to Extensions"))
        guide_button = Gtk.Button(
            icon_name="internet-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        guide_button.connect(
            "clicked",
            lambda _button: subprocess.Popen(
                get_spawn_command()
                + [
                    "xdg-open",
                    "https://github.com/qwersyk/Newelle/wiki/User-guide-to-Extensions",
                ]
            ),
        )
        guide_row.add_suffix(guide_button)
        guide_row.set_activatable_widget(guide_button)
        actions.add(guide_row)

        download_row = Adw.ActionRow(title=_("Download new Extensions"))
        download_button = Gtk.Button(
            icon_name="internet-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        download_button.connect(
            "clicked",
            lambda _button: subprocess.Popen(
                get_spawn_command()
                + ["xdg-open", "https://github.com/topics/newelle-extension"]
            ),
        )
        download_row.add_suffix(download_button)
        download_row.set_activatable_widget(download_button)
        actions.add(download_row)

        install_row = Adw.ActionRow(title=_("Install extension from file..."))
        install_button = Gtk.Button(
            label=_("Install"),
            valign=Gtk.Align.CENTER,
            css_classes=["suggested-action"],
        )
        install_button.connect("clicked", self.on_folder_button_clicked)
        install_row.add_suffix(install_button)
        install_row.set_activatable_widget(install_button)
        actions.add(install_row)

    def _convert_extension_constants(self, _constants):
        return "extension"

    def add_flatpak_warning_button(self, handler, row):
        if not handler.requires_sandbox_escape() or self.sandbox:
            return

        action_button = Gtk.Button(
            icon_name="warning-outline-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat", "error"],
        )
        action_button.connect("clicked", self.show_flatpak_sandbox_notice)
        if isinstance(row, Adw.ExpanderRow):
            row.add_action(action_button)
        else:
            row.add_suffix(action_button)

    def show_flatpak_sandbox_notice(self, _button=None):
        dialog = Adw.MessageDialog(
            title=_("Permission Error"),
            modal=True,
            transient_for=self._parent_window(),
            destroy_with_parent=True,
        )
        dialog.set_heading(_("Not enough permissions"))
        dialog.set_body_use_markup(True)
        dialog.set_body(
            _(
                "Newelle does not have enough permissions to run commands on your system, please run the following command"
            )
        )
        dialog.add_response("close", _("Understood"))
        dialog.set_default_response("close")
        dialog.set_extra_child(
            CopyBox(
                "flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle",
                "bash",
            )
        )
        dialog.set_close_response("close")
        dialog.set_response_appearance(
            "close",
            Adw.ResponseAppearance.DESTRUCTIVE,
        )
        dialog.connect(
            "response",
            lambda current_dialog, _response_id: current_dialog.destroy(),
        )
        dialog.present()

    def change_status(self, _switch, state, extension_id):
        if state:
            self.extensionloader.enable(extension_id)
        else:
            self.extensionloader.disable(extension_id)
        return False

    def _reload_user_extensions(self):
        loader = ExtensionLoader(
            self.extension_path,
            pip_path=self.pip_directory,
            extension_cache=self.extensions_cache,
            settings=self.settings,
        )
        loader.load_extensions()
        loader.set_ui_controller(self.controller.ui_controller)
        self.controller.set_extensionsloader(loader)
        self.extensionloader = loader

    def delete_extension(self, _button, extension_id):
        self.extensionloader.remove_extension(extension_id)
        self._reload_user_extensions()
        self.update()

    def on_folder_button_clicked(self, _button):
        file_filter = Gtk.FileFilter(
            name="Newelle Extensions",
            patterns=["*.py"],
        )
        dialog = Gtk.FileDialog(
            title=_("Import extension"),
            modal=True,
            default_filter=file_filter,
        )
        dialog.open(self._parent_window(), None, self.process_folder)

    def process_folder(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        if file is None:
            return

        file_path = file.get_path()
        filename = os.path.basename(file_path)
        self.extensionloader.add_extension(file_path)
        self._reload_user_extensions()

        added_extension = None
        for extension_id, extension_filename in self.extensionloader.filemap.items():
            if extension_filename == filename:
                added_extension = self.extensionloader.get_extension_by_id(extension_id)
                break

        if added_extension is None:
            self._add_toast(_("This is not an extension or it is not correct"))
            self.update()
            return

        Thread(target=added_extension.install, daemon=True).start()
        added_extension.set_setting(
            "reload_requested",
            added_extension.get_setting("reload_requested", False, 0) + 1,
        )
        self._add_toast(_("Extension added. New extensions will run"))
        self.update()


class Extension(Adw.Window):
    """Compatibility wrapper for callers that still open extensions directly."""

    def __init__(self, app):
        super().__init__(
            title=_("Extensions"),
            default_width=600,
            default_height=600,
            transient_for=app.win,
            modal=True,
        )
        overlay = Adw.ToastOverlay()
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        page = ExtensionPage(
            app,
            app.win.controller,
            toast_callback=overlay.add_toast,
        )
        toolbar_view.set_content(page)
        overlay.set_child(toolbar_view)
        self.set_content(overlay)
