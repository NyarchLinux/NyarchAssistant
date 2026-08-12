from gi.repository import Gtk, Pango


class StatusWidget(Gtk.Box):
    """Compact card shown in chat to confirm a completed tool action.

    Displays an icon, a title, an optional success badge and an optional
    subtitle (e.g. the result detail).
    """

    def __init__(self, title: str, icon_name: str = "emblem-default-symbolic",
                 subtitle: str = "", badge: str = "done"):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.add_css_class("card")
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(4)
        self.set_margin_end(4)

        icon = Gtk.Image(
            icon_name=icon_name,
            pixel_size=24,
            valign=Gtk.Align.CENTER,
            margin_start=12,
        )
        icon.add_css_class("accent")
        self.append(icon)

        text_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            valign=Gtk.Align.CENTER,
            margin_top=10,
            margin_bottom=10,
            hexpand=True,
        )

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title_label = Gtk.Label(
            label=title,
            halign=Gtk.Align.START,
            ellipsize=Pango.EllipsizeMode.END,
        )
        title_label.add_css_class("heading")
        title_box.append(title_label)

        if badge:
            badge_label = Gtk.Label(label=badge)
            badge_label.add_css_class("success")
            badge_label.add_css_class("caption")
            title_box.append(badge_label)

        text_box.append(title_box)

        if subtitle:
            sub_label = Gtk.Label(
                label=subtitle,
                halign=Gtk.Align.START,
                ellipsize=Pango.EllipsizeMode.END,
                max_width_chars=60,
            )
            sub_label.add_css_class("dim-label")
            sub_label.add_css_class("caption")
            text_box.append(sub_label)

        self.subtitle_label = sub_label
        self.title_label = title_label
        self.icon = icon

        self.append(text_box)

        check = Gtk.Image(
            icon_name="object-select-symbolic",
            pixel_size=16,
            valign=Gtk.Align.CENTER,
            margin_end=12,
        )
        check.add_css_class("success")
        self.append(check)

    def update(self, title=None, subtitle=None, icon_name=None):
        """Update the displayed title, subtitle or icon in place."""
        if title is not None and self.title_label is not None:
            self.title_label.set_label(title)
        if subtitle is not None and self.subtitle_label is not None:
            self.subtitle_label.set_label(subtitle)
        elif subtitle is not None and self.title_label is not None:
            # No subtitle label was created originally; nothing to update.
            pass
        if icon_name is not None and self.icon is not None:
            self.icon.set_from_icon_name(icon_name)
