import gettext

from gi.repository import Gtk, Pango

from ...utility.source_attribution import CitationSource

_ = gettext.gettext


def source_icon_name(source: CitationSource) -> str:
    return {
        "web": "internet-symbolic",
        "file": "text-x-generic-symbolic",
        "user": "avatar-default-symbolic",
        "memory": "document-open-recent-symbolic",
        "tool": "applications-utilities-symbolic",
    }.get(source.kind, "dialog-information-symbolic")


class SourceChip(Gtk.Button):
    """Compact inline button representing one cited source."""

    def __init__(self, source: CitationSource, on_open):
        super().__init__(css_classes=["flat", "pill", "source-chip"], valign=Gtk.Align.BASELINE)
        self.source = source
        self.set_tooltip_text(source.label)

        content = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            valign=Gtk.Align.BASELINE,
        )
        icon = Gtk.Image(icon_name=source_icon_name(source), pixel_size=14)
        icon.set_valign(Gtk.Align.CENTER)
        content.append(icon)
        label_width = min(max(len(source.label), 10), 20)
        content.append(Gtk.Label(
            label=source.label,
            width_chars=label_width,
            max_width_chars=20,
            ellipsize=Pango.EllipsizeMode.END,
            valign=Gtk.Align.BASELINE,
        ))
        self.set_child(content)
        self.connect("clicked", lambda _button: on_open(source))


class SourcesButton(Gtk.MenuButton):
    """Bottom-of-message button whose popover lists every cited source."""

    def __init__(self, sources: list[CitationSource], on_open):
        super().__init__(css_classes=["flat", "pill"], halign=Gtk.Align.START)
        self.sources = sources
        self.on_open = on_open
        self.set_always_show_arrow(False)
        self.set_tooltip_text(_("Show sources"))

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for source in sources[:3]:
            content.append(Gtk.Image(icon_name=source_icon_name(source), pixel_size=14))
        content.append(Gtk.Label(label=_("Sources")))
        content.append(Gtk.Image(icon_name="pan-down-symbolic", pixel_size=12))
        self.set_child(content)

        source_list = self._build_source_list()
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_width(True)
        scroll.set_propagate_natural_height(True)
        scroll.set_min_content_width(320)
        scroll.set_max_content_height(420)
        scroll.set_child(source_list)

        popover = Gtk.Popover()
        popover.set_child(scroll)
        self.set_popover(popover)
        self.popover = popover

    def _build_source_list(self) -> Gtk.Widget:
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_top=8,
            margin_bottom=8,
            margin_start=8,
            margin_end=8,
        )
        heading = Gtk.Label(
            label=_("Sources"),
            css_classes=["heading"],
            halign=Gtk.Align.START,
            margin_start=6,
            margin_end=6,
            margin_bottom=4,
        )
        box.append(heading)

        for source in self.sources:
            row_content = self._build_source_row(source)
            if source.target:
                row = Gtk.Button(css_classes=["flat"])
                row.set_child(row_content)
                row.connect("clicked", self._on_source_clicked, source)
            else:
                row = row_content
                row.set_margin_start(8)
                row.set_margin_end(8)
                row.set_margin_top(6)
                row.set_margin_bottom(6)
            box.append(row)
        return box

    def _build_source_row(self, source: CitationSource) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.append(Gtk.Image(icon_name=source_icon_name(source), pixel_size=18))

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(
            label=source.label,
            halign=Gtk.Align.START,
            xalign=0,
            max_width_chars=42,
            ellipsize=Pango.EllipsizeMode.END,
        )
        labels.append(title)
        if source.target:
            subtitle = Gtk.Label(
                label=source.target,
                css_classes=["dim-label", "caption"],
                halign=Gtk.Align.START,
                xalign=0,
                max_width_chars=48,
                ellipsize=Pango.EllipsizeMode.MIDDLE,
            )
            labels.append(subtitle)
        row.append(labels)
        return row

    def _on_source_clicked(self, _button, source: CitationSource):
        self.popover.popdown()
        self.on_open(source)
