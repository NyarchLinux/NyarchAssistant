"""GTK-free state tracking for grouped tool-call presentations."""

from dataclasses import dataclass


TOOL_CALL_STATES = frozenset(
    {"pending", "running", "completed", "error", "cancelled"}
)


@dataclass(frozen=True)
class ToolCallSnapshot:
    """Immutable view of one tool call in an iteration."""

    entry_id: int
    name: str
    status: str


@dataclass(frozen=True)
class ToolGroupSummary:
    """Derived state used by the compact group header."""

    count: int
    active_entry_id: int | None
    active_tool_name: str | None
    running_count: int
    failed_count: int
    cancelled_count: int


@dataclass
class _ToolCallEntry:
    entry_id: int
    name: str
    status: str = "pending"


class ToolCallGroupState:
    """Track ordered calls without importing GTK.

    The UI keeps one stable slot for every entry ID.  This small model makes
    status transitions and late async completions deterministic, including
    duplicate tool names and parallel completion order.
    """

    def __init__(self):
        self._next_id = 0
        self._entries: list[_ToolCallEntry] = []

    def add(self, name: str) -> int:
        entry_id = self._next_id
        self._next_id += 1
        self._entries.append(_ToolCallEntry(entry_id, name))
        return entry_id

    def adopt(self, name: str, status: str = "pending") -> int:
        """Add a call while preserving its presentation status.

        This is used when an already-rendered call is moved from a per-message
        group into a shared compact group during a preference toggle.
        """
        entry_id = self.add(name)
        self.set_status(entry_id, status)
        return entry_id

    def status_for(self, entry_id: int) -> str | None:
        entry = self._find(entry_id)
        return entry.status if entry is not None else None

    def update_name(self, entry_id: int, name: str) -> bool:
        entry = self._find(entry_id)
        if entry is None:
            return False
        entry.name = name
        return True

    def set_status(self, entry_id: int, status: str) -> bool:
        if status not in TOOL_CALL_STATES:
            raise ValueError(f"Unknown tool-call state: {status}")
        entry = self._find(entry_id)
        if entry is None:
            return False
        entry.status = status
        return True

    def remove(self, entry_id: int) -> bool:
        for index, entry in enumerate(self._entries):
            if entry.entry_id == entry_id:
                del self._entries[index]
                return True
        return False

    def reorder(self, entry_ids) -> None:
        by_id = {entry.entry_id: entry for entry in self._entries}
        ordered = [by_id[entry_id] for entry_id in entry_ids if entry_id in by_id]
        ordered_ids = {entry.entry_id for entry in ordered}
        ordered.extend(entry for entry in self._entries if entry.entry_id not in ordered_ids)
        self._entries = ordered

    def contains(self, entry_id: int) -> bool:
        return self._find(entry_id) is not None

    def snapshots(self) -> tuple[ToolCallSnapshot, ...]:
        return tuple(
            ToolCallSnapshot(entry.entry_id, entry.name, entry.status)
            for entry in self._entries
        )

    def summary(self) -> ToolGroupSummary:
        active = next(
            (
                entry
                for entry in self._entries
                if entry.status in {"pending", "running"}
            ),
            None,
        )
        return ToolGroupSummary(
            count=len(self._entries),
            active_entry_id=active.entry_id if active is not None else None,
            active_tool_name=active.name if active is not None else None,
            running_count=sum(
                entry.status in {"pending", "running"}
                for entry in self._entries
            ),
            failed_count=sum(entry.status == "error" for entry in self._entries),
            cancelled_count=sum(
                entry.status == "cancelled" for entry in self._entries
            ),
        )

    def _find(self, entry_id: int) -> _ToolCallEntry | None:
        return next(
            (entry for entry in self._entries if entry.entry_id == entry_id),
            None,
        )
