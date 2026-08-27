from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from importlib.resources import files
from typing import Any


APPLICATION_NAME = "Codex Credit Monitor"
MINIMIZED_MESSAGE = (
    "Still running. To reopen it, double-click the icon near the clock. "
    "If it is not visible, select the ^ button there."
)


class TrayController:
    def __init__(
        self,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        *,
        backend: Any | None = None,
        image: Any | None = None,
    ) -> None:
        if backend is None:
            import pystray

            backend = pystray
        self._on_open = on_open
        self._on_quit = on_quit
        self._started = False
        self._minimize_hint_shown = False
        self._icon = backend.Icon(
            "codex_credit_monitor",
            image if image is not None else create_icon_image(),
            APPLICATION_NAME,
            menu=backend.Menu(
                backend.MenuItem("Open", self._open, default=True),
                backend.MenuItem("Quit", self._quit),
            ),
        )

    def show(self) -> None:
        if self._started:
            self._icon.visible = True
            return
        self._icon.run_detached()
        self._started = True

    def hide(self) -> None:
        if self._started:
            self._icon.visible = False

    def stop(self) -> None:
        if self._started:
            self._icon.stop()
            self._started = False

    def show_minimize_hint(self) -> None:
        """Explain the notification-area behavior on the first minimize."""
        if self._minimize_hint_shown:
            return
        self._minimize_hint_shown = True
        try:
            self._icon.notify(MINIMIZED_MESSAGE, APPLICATION_NAME)
        except (AttributeError, NotImplementedError, OSError):
            # Notifications are a convenience; the tray icon remains usable
            # on backends that do not provide them.
            pass

    def set_utilization(self, percentage: Decimal) -> None:
        rounded = percentage.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        self._icon.title = f"{APPLICATION_NAME} ({rounded}%)"

    def _open(self, _icon: Any, _item: Any) -> None:
        self.hide()
        self._on_open()

    def _quit(self, _icon: Any, _item: Any) -> None:
        self._on_quit()


def create_icon_image() -> Any:
    from PIL import Image

    icon = files("codex_credit_monitor_windows").joinpath("assets", "app_icon.png")
    with icon.open("rb") as icon_file:
        return Image.open(icon_file).convert("RGBA")
