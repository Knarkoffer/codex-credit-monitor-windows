from decimal import Decimal
from unittest import TestCase

from codex_credit_monitor_windows.tray import TrayController


class FakeIcon:
    def __init__(self) -> None:
        self.visible = False
        self.run_count = 0
        self.stop_count = 0

    def run_detached(self) -> None:
        self.run_count += 1
        self.visible = True

    def stop(self) -> None:
        self.stop_count += 1


class FakeMenuItem:
    def __init__(self, text, action, default=False) -> None:
        self.text = text
        self.action = action
        self.default = default


class FakeBackend:
    MenuItem = FakeMenuItem

    def __init__(self) -> None:
        self.icon = FakeIcon()
        self.menu = ()

    def Menu(self, *items):
        self.menu = items
        return items

    def Icon(self, _name, _image, title, *, menu):
        self.icon.title = title
        self.menu = menu
        return self.icon


class TrayControllerTests(TestCase):
    def setUp(self) -> None:
        self.opened = 0
        self.quit = 0
        self.backend = FakeBackend()
        self.tray = TrayController(
            self._opened,
            self._quit,
            backend=self.backend,
            image=object(),
        )

    def _opened(self) -> None:
        self.opened += 1

    def _quit(self) -> None:
        self.quit += 1

    def test_show_starts_once_and_can_be_shown_again(self):
        self.tray.show()
        self.tray.hide()
        self.tray.show()

        self.assertEqual(self.backend.icon.run_count, 1)
        self.assertTrue(self.backend.icon.visible)

    def test_open_menu_hides_icon_and_requests_restore(self):
        self.tray.show()

        open_item = self.backend.menu[0]
        open_item.action(self.backend.icon, open_item)

        self.assertFalse(self.backend.icon.visible)
        self.assertEqual(self.opened, 1)
        self.assertTrue(open_item.default)

    def test_quit_menu_requests_application_close(self):
        quit_item = self.backend.menu[1]
        quit_item.action(self.backend.icon, quit_item)

        self.assertEqual(self.quit, 1)

    def test_stop_stops_started_icon(self):
        self.tray.show()
        self.tray.stop()

        self.assertEqual(self.backend.icon.stop_count, 1)

    def test_utilization_is_rounded_half_up_in_tooltip(self):
        examples = (
            (Decimal("81.71"), "Codex Credit Monitor (82%)"),
            (Decimal("14.5"), "Codex Credit Monitor (15%)"),
        )

        for percentage, expected in examples:
            with self.subTest(percentage=percentage):
                self.tray.set_utilization(percentage)
                self.assertEqual(self.backend.icon.title, expected)
