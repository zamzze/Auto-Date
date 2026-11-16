from base64 import b64decode
from collections import Counter
from contextlib import closing, nullcontext, redirect_stdout
from copy import deepcopy
from io import StringIO
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID
from xml.etree.ElementTree import fromstring, tostring

from autodate.domain import AdbUnavailable, Direction, Message, ReviewRequired, UiTimeout
from autodate.identity import normalize
from autodate.outreach_lite import LiteDriver, LiteKeyboard, Results, chat_titles, editor_exclusion, main, traverse
from autodate.outreach_lite_inbox import inbox_page
from autodate.outreach_lite_journal import RunJournal
from autodate.outreach_store import digest_text
from autodate.repository import SQLiteRepository
from autodate.simulator import chat_xml, hierarchy, node
from autodate.text_input import ADB_KEYBOARD_PACKAGE
from autodate.ui import Snapshot
from tests.test_outreach import Contact, InboxTransport, TEXT


KEYBOARD = f"{ADB_KEYBOARD_PACKAGE}/.AdbIME"
PREVIOUS_IME = "example.ime/.Original"
MEYO_HISTORY_FIXTURE = Path(__file__).with_name("fixtures") / "meyo_lite_nested_history.xml"
PREMIUM_CHAT_FIXTURE = MEYO_HISTORY_FIXTURE.with_name("meyo_lite_premium_chat.xml")
PREMIUM_ALIAS = "🥇🕹 Ejemplo 😏 💎"


class ImeDevice(InboxTransport):
    def __init__(self, contacts, *, write_reply=None, send_reply=None):
        super().__init__(contacts)
        self.write_reply = write_reply
        self.send_reply = send_reply
        self.timeline = []
        self.broadcasts = []
        self.current_ime = KEYBOARD
        self.enabled = {KEYBOARD, PREVIOUS_IME}
        self.initial_draft = ""
        self.draft_on_focus = ""
        self.wrong_header_on_focus = False

    def command(self, args):
        if args[:4] == ("shell", "pm", "list", "packages"):
            return f"package:{ADB_KEYBOARD_PACKAGE}\n".encode()
        if args[:3] == ("shell", "settings", "get"):
            return self.current_ime.encode()
        if args[:3] == ("shell", "ime", "list"):
            methods = {KEYBOARD, PREVIOUS_IME} if "-a" in args else self.enabled
            return "\n".join(sorted(methods)).encode()
        if args[:2] == ("shell", "ime"):
            action, method = args[2:]
            if action == "enable":
                self.enabled.add(method)
            elif action == "set":
                self.current_ime = method
            elif action == "disable":
                self.enabled.remove(method)
            else:
                raise AssertionError(args)
            return b""
        if args[:4] != ("shell", "am", "broadcast", "-a"):
            return super().command(args)
        if self.contact is None or not self.focused or self.current_ime != KEYBOARD:
            raise AssertionError("Broadcast outside the focused MeYo editor")
        self.broadcasts.append(args)
        action, package_flag, package, *extras = args[4:]
        assert (package_flag, package) == ("-p", ADB_KEYBOARD_PACKAGE)
        self.timeline.append(action)
        if action == "ADB_INPUT_B64":
            assert extras[:2] == ["--es", "msg"]
            self.draft = b64decode(extras[2], validate=True).decode("utf-8")
            self.writes.append(self.draft)
            reply = self.write_reply
        elif action == "ADB_EDITOR_CODE":
            assert extras == ["--ei", "code", "4"] and self.draft
            self.contact.messages.append(Message(Direction.OUT, self.draft))
            self.contacts.remove(self.contact)
            self.contacts.insert(0, self.contact)
            self.draft = ""
            reply = self.send_reply
        else:
            raise AssertionError(args)
        if isinstance(reply, BaseException):
            raise reply
        return b"Broadcast completed: result=0\r\n" if reply is None else reply

    def hierarchy(self, *, deadline=None):
        xml = fromstring(super().hierarchy(deadline=deadline))
        if self.contact is None:
            self.timeline.append("inbox")
        else:
            self.timeline.append("focus" if self.focused else "open")
            root = xml.find("node")
            node(root, box=(0, 66, 100, 150), clickable=True)
            if self.focused and self.wrong_header_on_focus:
                next(item for item in root if item.get("content-desc") == self.contact.name).set("content-desc", "Otro")
        return tostring(xml)

    def tap(self, point):
        if self.contact is None:
            super().tap(point)
            self.draft = self.initial_draft
        elif point == (50, 108):
            self.timeline.append("back")
            self.contact = None
            self.focused = False
            self.draft = ""
        elif not self.focused:
            super().tap(point)
            if self.draft_on_focus:
                self.draft = self.draft_on_focus
        else:
            raise AssertionError(f"Unexpected visual send tap at {point}")


class MissingComposerDevice(ImeDevice):
    def __init__(self, contacts, *, fail_return=False):
        super().__init__(contacts)
        self.fail_return = fail_return

    def hierarchy(self, *, deadline=None):
        xml = fromstring(super().hierarchy(deadline=deadline))
        if self.contact and self.contact.name == "Sin editor":
            root = xml.find("node")
            root.remove(next(item for item in root if item.get("class") == "android.widget.EditText"))
        elif self.fail_return and len(self.timeline) > 1 and self.timeline[-2] in {"back", "ambiguous_inbox"}:
            # The selected tab alone cannot verify a list with two competing containers.
            root = xml.find("node")
            listing = next(item for item in root if item.get("scrollable") == "true")
            root.append(deepcopy(listing))
            self.timeline[-1] = "ambiguous_inbox"
        return tostring(xml)


class LiteOutreachTests(unittest.TestCase):
    def setUp(self):
        self.output = self.enterContext(redirect_stdout(StringIO()))

    def execute(self, device, number, *, apply=False, text=TEXT, journal=None, inbox_only=False):
        if journal is None:
            journal_db = self.enterContext(closing(sqlite3.connect(":memory:")))
            journal = RunJournal(journal_db, digest_text(text), number, apply)
        driver = LiteDriver(device)
        driver.ui_timeout = 0.03
        driver.poll_interval = 0.001
        args = SimpleNamespace(number=number, apply=apply, delay_min=0, delay_max=0, inbox_only=inbox_only)
        results = Results()
        traverse(driver, LiteKeyboard(device.command), journal, text, args, results)
        self.assertEqual(device.profile_opens, 0)
        return results

    def run_cli(self, device, journal_path, *args):
        with (patch("autodate.outreach_lite.JOURNAL_PATH", journal_path),
              patch("autodate.outreach_lite.AdbTransport", return_value=device),
              patch("autodate.outreach_lite.device_lock", return_value=nullcontext()),
              patch.dict(os.environ, {"AUTODATE_OUTREACH_MESSAGE": TEXT})):
            return main(args)

    def test_existing_and_new_inbox_chats_are_eligible_without_a_historical_database(self):
        device = ImeDevice([Contact("Ya existente", messages=[Message(Direction.IN, "Hola")]), Contact("Nueva 🌻")])
        with TemporaryDirectory() as directory:
            journal = Path(directory, "data", "lite-runs.sqlite3")
            self.assertEqual(self.run_cli(device, journal, "--number", "2", "--apply", "--delay-max", "0", "--delay-min", "0"), 0)
            self.assertEqual(list(journal.parent.iterdir()), [journal])
        self.assertEqual(device.opens, ["Ya existente", "Nueva 🌻"])
        self.assertEqual(device.timeline.count("ADB_EDITOR_CODE"), 2)

    def test_only_currently_present_chats_are_eligible_even_after_disappearance(self):
        contact = Contact("Vuelve")
        device = ImeDevice([contact])
        self.assertEqual(self.execute(device, 1).simulated, 1)
        device.contacts.clear()
        self.assertEqual(self.execute(device, 1, apply=True).examined, 0)
        self.assertEqual(device.broadcasts, [])
        device.contacts.append(contact)
        self.assertEqual(self.execute(device, 1, apply=True).sent, 1)

    def test_inbox_only_dry_run_does_not_open_rows(self):
        device = ImeDevice([Contact("José")])
        result = self.execute(device, 1, inbox_only=True)
        self.assertEqual((result.simulated, device.timeline, device.broadcasts), (1, ["inbox"], []))

    def test_recovery_reuses_a_known_inbox_without_a_second_dump(self):
        device = ImeDevice([Contact("José")])
        driver = LiteDriver(device)
        screen = driver.snapshot()
        self.assertIs(driver.recover_inbox(initial=screen), screen)
        self.assertEqual(device.timeline, ["inbox"])

    def test_wrapped_history_and_explicit_hint_ignore_hidden_metadata(self):
        xml = fromstring(chat_xml([Message(Direction.IN, "Hola")]))
        root = xml.find("node")
        grid = next(item for item in root if item.get("scrollable") == "true")
        message = list(grid)[0]
        grid.remove(message)
        wrapper = node(grid, box=(0, 150, 720, 1418))
        wrapper.append(message)
        hidden = node(grid, desc="Etiqueta fuera de la pantalla", visible_to_user=False)
        hidden.set("visible-to-user", "false")
        editor = next(item for item in root if item.get("class") == "android.widget.EditText")
        editor.attrib.update({"text": "Escribe un mensaje", "hint": "Escribe un mensaje", "showing-hint-text": "true"})
        driver = LiteDriver(None)
        observed = driver.read_chat(Snapshot(tostring(xml)))
        self.assertIsNone(editor_exclusion(observed, TEXT))
        editor.attrib.pop("hint")
        with self.assertRaisesRegex(ReviewRequired, "composer_state_unknown"):
            editor_exclusion(driver.read_chat(Snapshot(tostring(xml))), TEXT)

    def meyo_history_editor(self, xml):
        driver = LiteDriver(None)
        driver.target_name = "Contacto"
        return driver.read_chat(Snapshot(tostring(xml)))

    def test_physical_layout_deduplicates_nested_views_and_ignores_the_reward_notice(self):
        xml = fromstring(MEYO_HISTORY_FIXTURE.read_bytes())
        editor = self.meyo_history_editor(xml)
        self.assertEqual([message.direction for message in editor.history.messages], [Direction.IN, Direction.IN])
        self.assertIsNone(editor_exclusion(editor, TEXT))

    def test_independent_history_views_with_identical_bounds_remain_ambiguous(self):
        xml = fromstring(MEYO_HISTORY_FIXTURE.read_bytes())
        shell = xml[0][0]
        shell.append(deepcopy(shell[2][0]))
        editor = self.meyo_history_editor(xml)
        self.assertIsNone(editor.history)
        self.assertIsNone(editor_exclusion(editor, TEXT))

    def test_nested_empty_history_requires_a_positive_empty_marker(self):
        xml = fromstring(MEYO_HISTORY_FIXTURE.read_bytes())
        grid = xml[0][0][2][0]
        for child in list(grid)[:-1]:
            grid.remove(child)
        editor = self.meyo_history_editor(xml)
        self.assertIsNone(editor.history)
        self.assertIsNone(editor_exclusion(editor, TEXT))
        marker = node(grid, kind="TextView", box=(160, 300, 560, 360), desc="No hay mensajes")
        grid.remove(marker)
        grid.insert(0, marker)
        editor = self.meyo_history_editor(xml)
        self.assertTrue(editor.history.empty_confirmed)
        self.assertIsNone(editor_exclusion(editor, TEXT))

    def test_history_uncertainty_does_not_affect_editor_eligibility(self):
        xml = fromstring(MEYO_HISTORY_FIXTURE.read_bytes())
        grid = xml[0][0][2][0]
        notice = grid[-1]
        notice.set("content-desc", "Elemento de historial que Lite no puede clasificar")
        editor = self.meyo_history_editor(xml)
        self.assertTrue(editor.history.uncertain)
        self.assertIsNone(editor_exclusion(editor, TEXT))

    def test_extractor_separates_numeric_names_badges_and_incomplete_cells(self):
        xml, root = hierarchy()
        node(root, desc="Mensaje", selected=True)
        listing = node(root, box=(0, 150, 720, 1535), scrollable=True)
        labels = ["1\nAna 🐱\nHola\n09:30", "9\nJosé\nHola\n09:30", "99+\n✨ Luz 2\nHola\n09:30",
                  "9\nTexto\n09:30", "99+", "1", "Alguien\nIncompleta", "7\nHola\n09:30", "💬 Único",
                  "1\nSin evidencia\nHola\n09:30"]
        for i, title in enumerate(labels):
            y = 155 + i * 130
            row = node(listing, box=(0, y, 720, y + 110), desc=title, clickable=True, scrollable=True)
            if i < 2:
                badge = node(row, kind="TextView", box=(675, y + 10, 710, y + 40), desc=title.splitlines()[0])
                badge.set("resource-id", "com.pita.oyem:id/unread_badge")
            elif i == 2:
                node(row, kind="ImageView", box=(27, y + 19, 131, y + 105))
                node(row, kind="ImageView", box=(646, y + 28, 692, y + 74))
        _, rows = inbox_page(Snapshot(tostring(xml)))
        self.assertEqual([(row.alias, row.unread_count) for row, _ in rows],
                         [("Ana 🐱", 1), ("José", 9), ("✨ Luz 2", 99), ("9", 0), ("7", 0), ("💬 Único", 0)])

    def test_empty_chat_writes_exact_unicode_and_sends_once_without_another_chat_dump(self):
        device = ImeDevice([Contact("José")])
        text = f"  {TEXT}\nSegunda línea $ `  "
        results = self.execute(device, 1, apply=True, text=text)
        self.assertEqual((results.sent, results.reviews, device.writes), (1, 0, [text]))
        self.assertEqual(device.timeline, ["inbox", "open", "focus", "ADB_INPUT_B64", "ADB_EDITOR_CODE", "back", "inbox"])
        self.assertIn("ENVIADO_SIN_VERIFICAR", self.output.getvalue())
        self.assertIn("Capturas XML: 3", self.output.getvalue())
        self.assertIn("XML reutilizado, análisis", self.output.getvalue())

    def test_apply_processes_immediately_and_deduplicates_after_reordering(self):
        device = ImeDevice([Contact("MeYo oficial", system=True), Contact("José"), Contact("Raúl"), Contact("Luis")])
        results = self.execute(device, 2, apply=True, text="ASCII also uses ADB Keyboard")
        self.assertEqual((results.examined, results.sent, device.opens), (2, 2, ["José", "Raúl"]))
        self.assertEqual(device.timeline.count("ADB_EDITOR_CODE"), 2)
        self.assertLess(device.timeline.index("ADB_EDITOR_CODE"), len(device.timeline) - 4)
        self.assertNotIn("SCROLL |", self.output.getvalue())

    def test_all_visits_both_directions_from_a_scrolled_dry_run(self):
        names = ["Uno", "Dos", "Tres", "Cuatro", "Cinco", "Seis", "Siete"]
        device = ImeDevice([Contact(name) for name in names])
        device.offset = 2
        results = self.execute(device, -1)
        self.assertEqual(device.opens, ["Tres", "Cuatro", "Cinco", "Dos", "Uno", "Seis", "Siete"])
        self.assertEqual((results.simulated, device.broadcasts), (len(names), []))
        self.assertIn("DRY_RUN", self.output.getvalue())

    def test_visible_homonyms_are_omitted_before_opening(self):
        device = ImeDevice([Contact("José Pérez"), Contact("JOSE\u0301   PE\u0301REZ"), Contact("Otro")])
        results = self.execute(device, 2)
        self.assertEqual((results.examined, results.skipped, results.simulated), (2, 1, 1))
        self.assertEqual(device.opens, ["Otro"])

    def test_truncated_header_requires_a_unique_visible_full_name(self):
        driver = LiteDriver(None)
        driver.target_name = "Sergio Verastegui"
        snapshot = Snapshot(chat_xml([Message(Direction.IN, "Hola")], alias="Sergio Verasteg..."))
        driver.visible_names = Counter({"sergio verastegui": 1, "otro": 1})
        self.assertIsNotNone(driver.read_chat(snapshot))
        driver.visible_names["sergio verastegui junior"] = 1
        with self.assertRaisesRegex(ReviewRequired, "inbox_alias_ambiguous"):
            driver.read_chat(snapshot)

    def test_premium_layout_matches_truncated_toolbar_title_and_reads_three_incoming_rows(self):
        screen = Snapshot(PREMIUM_CHAT_FIXTURE.read_bytes())
        driver = LiteDriver(None)
        driver.target_name = PREMIUM_ALIAS
        driver.visible_names = Counter({normalize(PREMIUM_ALIAS): 1})
        self.assertEqual(len(chat_titles(screen)), 1)
        editor = driver.read_chat(screen)
        self.assertIsNotNone(editor)
        self.assertEqual([message.direction for message in editor.history.messages], [Direction.IN] * 3)
        self.assertIsNone(editor_exclusion(editor, TEXT))

    def test_toolbar_keeps_identity_when_bubble_and_history_structure_change(self):
        xml = fromstring(PREMIUM_CHAT_FIXTURE.read_bytes())
        shell = xml[0][0]
        history = shell[3][0]
        # Even a clickable View bearing the target alias is a bubble below the toolbar.
        bubble = history[0][1]
        bubble.set("class", "android.view.View")
        bubble.set("content-desc", PREMIUM_ALIAS)
        driver = LiteDriver(None)
        driver.target_name = PREMIUM_ALIAS
        driver.visible_names = Counter({normalize(PREMIUM_ALIAS): 1})
        self.assertIsNotNone(driver.read_chat(Snapshot(tostring(xml))))
        shell[2].set("content-desc", "Otra persona")
        self.assertEqual(driver.inspect_chat(Snapshot(tostring(xml))), (None, "recipient_header_unverified"))
        shell[2].set("content-desc", PREMIUM_ALIAS)
        shell.remove(shell[3])
        editor = driver.read_chat(Snapshot(tostring(xml)))
        self.assertIsNotNone(editor)
        self.assertIsNone(editor.history)
        shell.remove(shell[0])
        self.assertEqual(driver.inspect_chat(Snapshot(tostring(xml))), (None, "history_structure_unverified"))

    def test_observation_budget_preserves_the_structural_cause_but_dump_failure_stays_distinct(self):
        xml = fromstring(PREMIUM_CHAT_FIXTURE.read_bytes())
        shell = xml[0][0]
        shell.remove(next(item for item in shell if item.get("class") == "android.widget.EditText"))
        screen = Snapshot(tostring(xml))
        driver = LiteDriver(None)
        driver.target_name = PREMIUM_ALIAS
        driver.visible_names = Counter({normalize(PREMIUM_ALIAS): 1})
        for transport_code, observed, expected in (
            ("ui_observation_budget_exhausted", screen, "composer_unverified"),
            ("ui_timeout", Snapshot(b"<hierarchy/>"), "ui_observation_budget_exhausted"),
            ("ui_dump_failed", screen, "ui_dump_failed"),
        ):
            with self.subTest(expected=expected):
                def wait_then_fail(condition):
                    self.assertIsNone(condition(observed))
                    raise UiTimeout(transport_code)

                with patch.object(driver, "wait", side_effect=wait_then_fail):
                    with self.assertRaisesRegex((ReviewRequired, UiTimeout), expected):
                        driver.wait_for_chat()

    def test_missing_editor_recovers_and_continues_without_reopening_previous_contacts(self):
        contacts = [Contact("Anterior"), Contact("Sin editor", messages=[Message(Direction.IN, "Hola")]), Contact("Siguiente")]
        device = MissingComposerDevice(contacts)
        db = self.enterContext(closing(sqlite3.connect(":memory:")))
        journal = RunJournal(db, digest_text(TEXT), 3, True)
        results = self.execute(device, 3, apply=True, journal=journal)
        self.assertEqual((results.sent, results.reviews, results.recoveries), (2, 1, 1))
        self.assertEqual(device.opens, ["Anterior", "Sin editor", "Siguiente"])
        self.assertEqual(device.writes, [TEXT, TEXT])
        self.assertEqual(db.execute("SELECT status, reason, ime_attempted FROM lite_visits WHERE position=2").fetchone(),
                         ("REVISIÓN", "composer_unverified", 0))

    def test_recovery_stops_when_the_selected_messages_tab_has_an_ambiguous_list(self):
        contacts = [Contact("Sin editor", messages=[Message(Direction.IN, "Hola")]), Contact("Siguiente")]
        device = MissingComposerDevice(contacts, fail_return=True)
        with self.assertRaisesRegex(ReviewRequired, "return_screen_unverified"):
            self.execute(device, 2, apply=True)
        self.assertEqual(device.opens, ["Sin editor"])
        self.assertEqual(device.timeline.count("back"), 1)
        self.assertEqual(device.broadcasts, [])

    def test_historical_unlinked_and_old_revoked_contacts_follow_the_current_inbox(self):
        with TemporaryDirectory() as directory:
            registry = Path(directory, "outreach.sqlite3")
            journal = Path(directory, "lite-runs.sqlite3")
            with SQLiteRepository(registry) as repository:
                repository.db.execute("INSERT INTO outreach_chats (nickname, history, updated_at) VALUES ('Histórico', '[]', 'now')")
                repository.db.execute("INSERT INTO outreach_chats (meyo_id, nickname, history, updated_at, revoked_at) VALUES ('12345', 'Volvió', '[]', 'now', 'old')")
                repository.db.execute("INSERT INTO meyo_consent VALUES ('12345', 0, NULL, 'old')")
            archive_bytes = registry.read_bytes()
            device = ImeDevice([Contact("Histórico"), Contact("Volvió", messages=[Message(Direction.IN, "No me escribas más")])])
            with patch("autodate.outreach_lite.sqlite3.connect", wraps=sqlite3.connect) as connect:
                self.assertEqual(self.run_cli(device, journal, "--number", "2", "--apply", "--delay-max", "0", "--delay-min", "0"), 0)
            connect.assert_called_once_with(journal, timeout=0.5)
            self.assertEqual(registry.read_bytes(), archive_bytes)
            self.assertEqual(device.opens, ["Histórico", "Volvió"])
            self.assertEqual(device.timeline.count("ADB_EDITOR_CODE"), 2)

    def test_same_visible_outgoing_is_skipped_on_another_run(self):
        device = ImeDevice([Contact("José", messages=[Message(Direction.OUT, TEXT)])])
        results = self.execute(device, 1, apply=True)
        self.assertEqual((results.sent, results.skipped, device.broadcasts), (0, 1, []))

    def test_existing_draft_before_or_during_focus_blocks_write(self):
        for field in ("initial_draft", "draft_on_focus"):
            with self.subTest(field=field):
                device = ImeDevice([Contact("José")])
                setattr(device, field, "Borrador existente")
                results = self.execute(device, 1, apply=True)
                self.assertEqual((results.sent, results.reviews, device.broadcasts), (0, 1, []))

    def test_wrong_chat_after_focus_blocks_both_broadcasts(self):
        device = ImeDevice([Contact("José")])
        device.wrong_header_on_focus = True
        results = self.execute(device, 1, apply=True)
        self.assertEqual((results.sent, results.reviews), (0, 1))
        self.assertEqual(device.broadcasts, [])

    def test_dry_run_can_start_from_another_chat_with_a_focused_editor(self):
        device = ImeDevice([Contact("José")])
        device.contact = Contact("Chat previo")
        device.focused = True
        device.draft = "Borrador del operador"
        results = self.execute(device, 1)
        self.assertEqual((results.simulated, device.opens, device.broadcasts), (1, ["José"], []))
        self.assertEqual(device.timeline[:3], ["focus", "back", "inbox"])

    def test_uncertain_write_blocks_send(self):
        device = ImeDevice([Contact("José")], write_reply=b"Broadcast completed: result=1")
        results = self.execute(device, 1, apply=True)
        self.assertEqual((results.sent, results.reviews, device.timeline.count("ADB_EDITOR_CODE")), (0, 1, 0))

    def test_uncertain_ime_result_can_follow_delivery_and_is_never_retried(self):
        device = ImeDevice([Contact("José")], send_reply=AdbUnavailable("adb_transport_timeout"))
        results = self.execute(device, -1, apply=True)
        self.assertEqual((results.examined, results.sent, results.reviews), (1, 0, 1))
        self.assertEqual(device.timeline.count("ADB_EDITOR_CODE"), 1)
        self.assertIn("resultado IME incierto", self.output.getvalue())

    def test_batch_stops_after_two_consecutive_adb_errors(self):
        device = ImeDevice([Contact(name) for name in ("Uno", "Dos", "Tres")],
                           write_reply=AdbUnavailable("adb_transport_timeout"))
        with self.assertRaisesRegex(ReviewRequired, "errores consecutivos"):
            self.execute(device, -1, apply=True)
        self.assertEqual(device.opens, ["Uno", "Dos"])
        self.assertEqual(device.timeline.count("ADB_EDITOR_CODE"), 0)

    def test_cli_restores_keyboard_on_normal_exit_and_ctrl_c_during_send(self):
        with TemporaryDirectory() as directory:
            for send_reply, expected in ((None, 0), (KeyboardInterrupt(), 130)):
                with self.subTest(exit_code=expected):
                    device = ImeDevice([Contact("José")], send_reply=send_reply)
                    device.current_ime = PREVIOUS_IME
                    device.enabled = {PREVIOUS_IME}
                    journal = Path(directory, "lite-runs.sqlite3")
                    self.assertEqual(self.run_cli(device, journal, "--number", "1", "--apply", "--activate-adb-keyboard"), expected)
                    self.assertEqual((device.current_ime, device.enabled), (PREVIOUS_IME, {PREVIOUS_IME}))
                    self.assertEqual(device.timeline.count("ADB_EDITOR_CODE"), 1)
            self.assertIn("Promedio por conversación", self.output.getvalue())

    def test_resume_blocks_uncertain_ime_with_equivalent_normalized_name(self):
        names = [Contact("José 😺"), Contact("JOSE\u0301   😺"), Contact("Siguiente")]
        with TemporaryDirectory() as directory:
            path = Path(directory, "lite.sqlite3")
            with closing(sqlite3.connect(path)) as journal_db:
                journal = RunJournal(journal_db, digest_text(TEXT), 2, True)
                interrupted = ImeDevice([names[0]], send_reply=KeyboardInterrupt())
                with self.assertRaises(KeyboardInterrupt):
                    self.execute(interrupted, 2, apply=True, journal=journal)
                batch = UUID(journal.id)
                self.assertEqual(journal_db.execute("SELECT status, ime_attempted FROM lite_visits").fetchone(), ("REVISIÓN", 1))
            with closing(sqlite3.connect(path)) as journal_db:
                resumed = RunJournal(journal_db, digest_text(TEXT), 2, True, resume=batch)
                device = ImeDevice(names[1:])
                result = self.execute(device, 2, apply=True, journal=resumed)
                self.assertEqual((result.examined, result.sent, device.opens), (1, 1, ["Siguiente"]))
                self.assertEqual(device.timeline.count("ADB_EDITOR_CODE"), 1)
                self.assertEqual(device.contacts[0].name, "Siguiente")
                with self.assertRaisesRegex(ReviewRequired, "mensaje, límite o modo"):
                    RunJournal(journal_db, digest_text("otro texto"), 2, True, resume=batch)

    def test_changing_inbox_stops_at_scroll_budget(self):
        device = ImeDevice([Contact(f"Persona {i}") for i in range(12)])
        with patch("autodate.outreach_lite.MAX_SCROLLS", 4):
            with self.assertRaisesRegex(ReviewRequired, "inbox_scroll_limit"):
                self.execute(device, -1)
