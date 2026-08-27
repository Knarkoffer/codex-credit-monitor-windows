from decimal import Decimal
from unittest import TestCase

from codex_credit_monitor_windows.tray import TrayController, create_icon_image


class IconImageTests(TestCase):
    def test_packaged_icon_is_loaded_with_transparency(self):
        image = create_icon_image()

        self.assertEqual(image.size, (512, 512))
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getpixel((0, 0))[3], 0)


class FakeIcon:
    def __init__(self) -> None:
        self.visible = False
        self.run_count = 0
        self.stop_count = 0
        self.notifications = []

    def run_detached(self) -> None:
        self.run_count += 1
        self.visible = True

    def stop(self) -> None:
        self.stop_count += 1

    def notify(self, message, title) -> None:
        self.notifications.append((message, title))


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

    def test_minimize_hint_is_shown_only_once(self):
        self.tray.show_minimize_hint()
        self.tray.show_minimize_hint()

        self.assertEqual(len(self.backend.icon.notifications), 1)
        message, title = self.backend.icon.notifications[0]
        self.assertIn("^", message)
        self.assertEqual(title, "Codex Credit Monitor")

    def test_utilization_is_rounded_half_up_in_tooltip(self):
        examples = (
            (Decimal("81.71"), "Codex Credit Monitor (82%)"),
            (Decimal("14.5"), "Codex Credit Monitor (15%)"),
        )

        for percentage, expected in examples:
            with self.subTest(percentage=percentage):
                self.tray.set_utilization(percentage)
                self.assertEqual(self.backend.icon.title, expected)
