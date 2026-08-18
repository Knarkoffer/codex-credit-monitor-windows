from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


APPLICATION_NAME = "Codex Credit Monitor"


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

    def set_utilization(self, percentage: Decimal) -> None:
        rounded = percentage.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        self._icon.title = f"{APPLICATION_NAME} ({rounded}%)"

    def _open(self, _icon: Any, _item: Any) -> None:
        self.hide()
        self._on_open()

    def _quit(self, _icon: Any, _item: Any) -> None:
        self._on_quit()


def create_icon_image() -> Any:
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    drawing = ImageDraw.Draw(image)
    drawing.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(32, 33, 35, 255))
    drawing.arc((14, 14, 50, 50), start=-90, end=205, fill=(16, 163, 127, 255), width=7)
    drawing.ellipse((27, 27, 37, 37), fill=(245, 245, 245, 255))
    return image
