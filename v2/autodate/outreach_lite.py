import argparse
from base64 import b64encode
from collections import Counter
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import time
from uuid import UUID
from xml.etree.ElementTree import Element

from .domain import AdbUnavailable, Direction, ReviewRequired, UiTimeout, WorkerError
from .identity import normalize
from .meyo_semantics import HistoryEvidence, resource_name
from .outreach import OutreachDriver, ZTE_SERIAL, inbox_row_point
from .outreach_lite_inbox import inbox_page
from .outreach_lite_journal import RunJournal
from .outreach_store import digest_text
from .text_input import ADB_KEYBOARD_METHODS, ADB_KEYBOARD_PACKAGE, AdbKeyboardInput
from .transport import AdbTransport, device_lock
from .ui import Bounds, bounds, flag, label, parse_chat_history, single


MAX_SCROLLS = 250
MAX_RUN_SECONDS = 3600
MAX_CONSECUTIVE_ADB_ERRORS = 2
JOURNAL_PATH = Path(__file__).resolve().parents[1] / "data" / "lite-runs.sqlite3"
REWARD_EXPIRY_NOTICE = re.compile(r"Sin respuesta en [0-9]+ minutos, la recompensa ya no está disponible\.")
OBSERVATION_BUDGET_ERRORS = {"ui_timeout", "ui_observation_budget_exhausted"}


def event(name, detail):
    print(f"[{datetime.now():%H:%M:%S}] {name} | {detail}", flush=True)


def elapsed(start):
    return f"{time.perf_counter() - start:.1f} s"


class AdbTimings:
    def __init__(self):
        self.calls = Counter()
        self.seconds = Counter()

    @contextmanager
    def measure(self, operation, *, attempt=1):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.calls[operation] += 1
            self.seconds[operation] += time.perf_counter() - started

    def report(self):
        for operation, count in self.calls.items():
            seconds = self.seconds[operation]
            event("ADB TIEMPOS", f"{operation} | {count} llamadas | {seconds:.2f} s total | {seconds / count:.2f} s promedio")


def matching_outgoing(messages, text):
    return any(message.direction == Direction.OUT and message.text == text for message in messages)


def chat_titles(screen):
    area = screen.viewport
    if area is None:
        return []
    candidates = [node for node in screen.nodes if flag(node, "clickable") and label(node)
                  and node.get("class") in {"android.view.View", "android.widget.TextView"}
                  and node.get("visible-to-user") != "false"
                  and area.point(x=0.10)[0] <= bounds(node).left < area.point(x=0.75)[0]
                  and area.point(y=0.04)[1] < bounds(node).top < area.point(y=0.15)[1]
                  and bounds(node).width < area.width * 0.70]
    # A tall clickable bubble can start in the same screen band as the title.
    # The actual title shares the toolbar with the back control.
    anchored = [node for node in candidates if back_control(screen, bounds(node))]
    return anchored or candidates


def editor_fields(screen):
    return [node for node in screen.nodes if node.get("class") == "android.widget.EditText"
            and node.get("visible-to-user") != "false"]


def back_control(screen, header):
    controls = [box for node in screen.nodes if (box := bounds(node)) and flag(node, "clickable")
                and node.get("visible-to-user") != "false"
                and flag(node, "enabled") and box.left <= screen.viewport.point(x=0.05)[0]
                and box.right <= header.left and box.top <= header.top < header.bottom <= box.bottom
                and normalize(label(node)) in {"", "atrás", "volver", "back"}]
    return controls[0] if len(controls) == 1 else None


def verified_inbox(screen):
    if not screen.inbox_area():
        return False
    try:
        inbox_page(screen)
    except ReviewRequired:
        return False
    return True


class LiteKeyboard(AdbKeyboardInput):
    def broadcast(self, action, extras):
        if self.current() not in ADB_KEYBOARD_METHODS:
            raise ReviewRequired("unicode_ime_inactive")
        result = self.command(("shell", "am", "broadcast", "-a", action, "-p", ADB_KEYBOARD_PACKAGE, *extras))
        if b"Broadcast completed: result=0" not in result.splitlines():
            raise ReviewRequired(f"{action.lower()}_unverified")

    def write(self, text):
        payload = b64encode(text.encode("utf-8")).decode("ascii")
        self.broadcast("ADB_INPUT_B64", ("--es", "msg", payload))

    def send(self):
        self.broadcast("ADB_EDITOR_CODE", ("--ei", "code", "4"))


@dataclass(frozen=True)
class Editor:
    header: Bounds
    composer: Bounds
    composer_text: str | None
    focused: bool
    history: HistoryEvidence | None


def editor_text(node):
    text = node.get("text")
    if node.get("showing-hint-text") == "true":
        return "" if text == "" or (node.get("hint") and text == node.get("hint")) else None
    return text


def visible_children(node):
    return [child for child in node if bounds(child) and child.get("visible-to-user") != "false"]


def history_leaf(grid):
    # MeYo exposes a short list through nested, coextensive accessibility Views.
    while len(children := visible_children(grid)) == 1:
        child = children[0]
        if (child.get("class") != "android.view.View" or label(child) or resource_name(child) or flag(child, "clickable")
                or not len(child) or bounds(child) != bounds(grid)):
            break
        grid = child
    return grid


def is_reward_expiry_notice(node, grid):
    box, area = bounds(node), bounds(grid)
    return (node.get("class") == "android.view.View" and not len(node) and not flag(node, "clickable")
            and flag(node, "focusable") and area.contains(box) and box.left == area.left and box.right == area.right
            and REWARD_EXPIRY_NOTICE.fullmatch(label(node).strip()) is not None)


def visible_history(grid):
    visible = visible_children(grid)
    if visible and is_reward_expiry_notice(visible[-1], grid):
        visible.pop()
    observed = Element(grid.tag, grid.attrib)
    observed.extend(visible)
    history = parse_chat_history(observed)
    if not (history.uncertain or history.has_authentic or history.empty_marker) and len(visible) == 1:
        child = visible[0]
        if not label(child) and bounds(child).width >= bounds(grid).width * .9 and len(child):
            nested = parse_chat_history(child)
            if nested.has_authentic or nested.empty_marker:
                return nested
    return history


def read_history(screen, header, bottom):
    areas = [node for node in screen.nodes if not label(node) and node.get("visible-to-user") != "false"
             and (box := bounds(node)) and box.width >= screen.viewport.width * 0.90
             and header.bottom <= box.top < box.bottom <= bottom]
    grids = {history_leaf(node) for node in areas if flag(node, "scrollable")}
    if grids:
        return visible_history(next(iter(grids))) if len(grids) == 1 else None

    # Deduplicate by descendant identity, so independent sibling lists remain ambiguous.
    regions = {history_leaf(node) for node in areas if node.get("class") == "android.view.View" and len(node)}
    candidates = [item for node in regions if (item := visible_history(node)).empty_marker or item.has_authentic]
    return candidates[0] if len(candidates) == 1 else None


class LiteDriver(OutreachDriver):
    def __init__(self, transport):
        super().__init__(transport, poll_interval=0.2)
        self.chat_screen = None
        self.observed_screen = None
        self.visible_names = ()
        self.snapshots = 0

    def snapshot(self, *, deadline=None):
        self.snapshots += 1
        self.observed_screen = super().snapshot(deadline=deadline)
        return self.observed_screen

    def prepare_inbox(self):
        try:
            return super().prepare_inbox()
        except ReviewRequired as exc:
            if exc.code != "message_tab_unverified":
                raise
            return self.recover_inbox(initial=self.observed_screen)

    def read_chat(self, screen, *, navigation_only=False):
        return self.inspect_chat(screen, navigation_only=navigation_only)[0]

    def inspect_chat(self, screen, *, navigation_only=False):
        if screen.viewport is None or screen.inbox_area():
            return None, None
        if screen.profile_open():
            return None, "recipient_header_unverified"
        headers = chat_titles(screen)
        expected = normalize(self.target_name)
        header = single([node for node in headers if normalize(label(node)) == expected], "chat_header_ambiguous")
        if header is None and len(headers) == 1:
            title = label(headers[0]).strip()
            if title.endswith(("...", "…")):
                prefix = normalize(title.rstrip(".…"))
                matches = [name for name in self.visible_names if prefix and name.startswith(prefix)]
                if matches == [expected]:
                    header = headers[0]
                elif expected in matches:
                    raise ReviewRequired("inbox_alias_ambiguous")
        if header is None:
            issue = "recipient_header_unverified" if headers or editor_fields(screen) else None
            return None, issue
        header_box = bounds(header)
        fields = editor_fields(screen)
        if len(fields) != 1 or not flag(fields[0], "enabled") or bounds(fields[0]).top < header_box.bottom:
            return None, "composer_unverified"
        field = fields[0]
        composer = bounds(field)
        back = back_control(screen, header_box)
        history = None if navigation_only and back else read_history(screen, header_box, composer.top)
        if not back and history is None:
            return None, "history_structure_unverified"
        self.chat_screen = screen
        return Editor(header_box, composer, editor_text(field), flag(field, "focused"), history), None

    def wait_for_chat(self, *, focused=False):
        issue = None

        def ready(screen):
            nonlocal issue
            chat, issue = self.inspect_chat(screen)
            if chat is not None and focused and not chat.focused:
                issue = "composer_unverified"
                return None
            return chat

        try:
            return self.wait(ready)[1]
        except UiTimeout as exc:
            if exc.code not in OBSERVATION_BUDGET_ERRORS:
                raise
            if issue:
                raise ReviewRequired(issue) from exc
            if exc.code == "ui_timeout":
                raise UiTimeout("ui_observation_budget_exhausted") from exc
            raise

    def focus_editor(self, editor):
        self.observed_screen = None
        self.tap(editor.composer.point())
        return self.wait_for_chat(focused=True)

    def open_row(self, row, point, visible_names):
        self.target_name = row.alias
        self.visible_names = visible_names

        self.observed_screen = None
        self.tap(point)
        return self.wait_for_chat()

    def recover_inbox(self, *, initial=None):
        try:
            if initial is not None and (inbox := self.return_from_screen(initial)) is not None:
                return inbox
            inbox = self.return_from_screen(self.snapshot())
        except UiTimeout as exc:
            if exc.code in OBSERVATION_BUDGET_ERRORS:
                raise ReviewRequired("return_screen_unverified") from exc
            raise
        except AdbUnavailable as exc:
            try:
                connected = self.transport.command(("get-state",)).strip() == b"device"
            except AdbUnavailable:
                connected = False
            if not connected:
                raise ReviewRequired("adb_device_unavailable") from exc
            raise
        if inbox is None:
            raise ReviewRequired("return_screen_unverified")
        return inbox

    def return_from_screen(self, screen):
        if screen.inbox_area():
            return screen if verified_inbox(screen) else None
        if not screen.profile_open():
            titles, fields = chat_titles(screen), editor_fields(screen)
            if len(titles) == 1:
                header = bounds(titles[0])
                if control := back_control(screen, header):
                    has_editor = len(fields) == 1 and flag(fields[0], "enabled") and bounds(fields[0]).top >= header.bottom
                    history = None if has_editor else read_history(screen, header, screen.viewport.bottom)
                    if has_editor or (history and (history.has_authentic or history.empty_confirmed)):
                        self.observed_screen = None
                        self.tap(control.point())
                        return self.wait(verified_inbox)[0]
        try:
            chat = self.read_chat(screen, navigation_only=True)
        except ReviewRequired:
            return None
        if chat is not None:
            self.chat_screen = screen
            return self.leave_chat(chat)
        return None

    def leave_chat(self, chat):
        # The visible back control exits directly on the ZTE, including with a focused editor.
        screen = self.chat_screen
        self.chat_screen = None
        self.observed_screen = None
        if control := back_control(screen, chat.header):
            self.tap(control.point())
        else:
            self.back()
            if chat.focused:
                def keyboard_closed(current):
                    active = self.read_chat(current, navigation_only=True)
                    return verified_inbox(current) or (active and active.composer and (
                        not active.focused or active.composer.top > chat.composer.top))

                screen, _ = self.wait(keyboard_closed)
                if verified_inbox(screen):
                    return screen
                self.back()
        return self.wait(verified_inbox)[0]

    def scroll_page(self, area, *, toward_top):
        upper = area.point(x=0.30, y=0.25)
        lower = area.point(x=0.30, y=0.79)
        self.swipe(*((upper, lower) if toward_top else (lower, upper)))
        return self.wait(lambda current: current.inbox_area())[0]


@dataclass
class Results:
    examined: int = 0
    sent: int = 0
    skipped: int = 0
    simulated: int = 0
    reviews: int = 0
    next_send_at: float = 0
    recipient_seconds: float = 0
    adb_errors: int = 0
    opened: int = 0
    recoveries: int = 0
    skips_by_cause: Counter = field(default_factory=Counter)
    reviews_by_cause: Counter = field(default_factory=Counter)


def editor_exclusion(editor, text):
    if editor.composer_text is None:
        raise ReviewRequired("composer_state_unknown")
    if editor.composer_text:
        raise ReviewRequired("composer_not_empty")
    # The inbox supplies eligibility. Readable bubbles can only signal an existing duplicate.
    if editor.history and matching_outgoing(editor.history.messages, text):
        return "el mismo mensaje saliente ya está visible"
    return None


def visit(driver, keyboard, journal, row, point, aliases, text, args, results, position):
    started = time.perf_counter()
    first_snapshot = driver.snapshots
    recipient = f"{position}/{args.number if args.number > 0 else 'todas'} | {row.alias!r}"
    name = normalize(row.alias)
    name_hash = digest_text(name)

    def skip(reason, category):
        journal.finish(name_hash, "OMITIDO", reason)
        results.skipped += 1
        results.skips_by_cause[category] += 1
        event("OMITIDO", f"{recipient} | {reason} | {elapsed(started)}")

    if aliases[name] > 1:
        skip("nombre duplicado en la bandeja visible", "identidad")
        return None
    if getattr(args, "inbox_only", False):
        journal.finish(name_hash, "DRY_RUN", "solo_bandeja")
        results.simulated += 1
        event("DRY_RUN", f"Fila {position} observada en bandeja")
        return None

    stage = "apertura"
    stage_started = time.perf_counter()
    chat = None
    recover = False
    event("CHAT", f"{recipient} | Abriendo...")
    try:
        chat = driver.open_row(row, point, aliases)
        results.opened += 1
        event("CHAT ABIERTO", f"{recipient} | {elapsed(stage_started)}")
        stage = "verificación del editor"
        stage_started = time.perf_counter()
        if reason := editor_exclusion(chat, text):
            skip(reason, "deduplicación")
        else:
            journal.bind(name_hash, name_hash, "encabezado_verificado")
            if journal.attempted(name_hash):
                skip("identidad con acción IME previa en el lote", "deduplicación")
            elif not args.apply:
                journal.finish(name_hash, "DRY_RUN")
                results.simulated += 1
                event("DRY_RUN", f"{recipient} | Fila, encabezado y editor vacío verificados")
            else:
                stage = "enfoque"
                stage_started = time.perf_counter()
                fast_focus_applied = False
                if not chat.focused:
                    recover = True
                    if args.fast_focus:
                        # The preceding XML already verified recipient, composer bounds,
                        # and empty text. Tap that same composer without another XML dump.
                        # The focus result is intentionally NOT marked as verified.
                        driver.observed_screen = None
                        driver.tap(chat.composer.point())
                        fast_focus_applied = True
                    else:
                        chat = driver.focus_editor(chat)
                    recover = False
                event("ENFOQUE", f"{recipient} | {elapsed(stage_started)}"
                      + (" | RAPIDO_SIN_VERIFICAR" if fast_focus_applied else ""))
                if (remaining := results.next_send_at - time.perf_counter()) > 0:
                    event("ESPERA", f"{recipient} | {remaining:.1f} s")
                    time.sleep(remaining)
                    recover = True
                    chat, issue = driver.inspect_chat(driver.snapshot())
                    if chat is None or not chat.focused:
                        raise ReviewRequired(issue or "composer_unverified")
                    recover = False
                if reason := editor_exclusion(chat, text):
                    skip(reason, "deduplicación")
                else:
                    stage = "escritura"
                    stage_started = time.perf_counter()
                    event("ESCRIBIENDO", recipient)
                    recover = True
                    driver.observed_screen = None
                    keyboard.write(text)
                    event("ESCRITO", f"{recipient} | {elapsed(stage_started)}")

                    stage = "IME Send"
                    journal.reserve_ime(name_hash)
                    event("IME SEND", recipient)
                    stage_started = time.perf_counter()
                    results.next_send_at = stage_started + random.uniform(args.delay_min, args.delay_max)
                    keyboard.send()
                    ime_time = elapsed(stage_started)
                    journal.finish(name_hash, "ENVIADO_SIN_VERIFICAR")
                    results.sent += 1
                    event("ENVIADO_SIN_VERIFICAR", f"{recipient} | IME {ime_time} | {elapsed(started)}")
                    recover = False
        results.adb_errors = 0
    except KeyboardInterrupt:
        results.reviews += 1
        results.reviews_by_cause["interrupción"] += 1
        event("REVISIÓN", f"{recipient} | interrupción durante {stage} | {elapsed(started)}")
        raise
    except WorkerError as exc:
        general = isinstance(exc, AdbUnavailable) or exc.code.startswith(("adb_input_", "adb_editor_", "unicode_ime_"))
        results.adb_errors = results.adb_errors + 1 if general else 0
        journal.finish(name_hash, "REVISIÓN", exc.code)
        results.reviews += 1
        results.reviews_by_cause[exc.code] += 1
        condition = "resultado IME incierto" if stage == "IME Send" else stage
        event("REVISIÓN", f"{recipient} | {condition} ({exc.code}) | {elapsed(stage_started)}")
    event("CHAT", f"{recipient} | Volviendo a la bandeja...")
    returning = time.perf_counter()
    if not recover and chat is not None:
        try:
            screen = driver.leave_chat(chat)
        except (UiTimeout, AdbUnavailable):
            recover = True
    if recover or chat is None:
        event("RECUPERACIÓN", f"{recipient} | Verificando una pantalla conocida")
        screen = driver.recover_inbox(initial=driver.observed_screen)
        results.recoveries += 1
    event("REGRESO", f"{recipient} | {elapsed(returning)}")
    event("CHAT COMPLETADO", f"{recipient} | Total {elapsed(started)} | Capturas XML: {driver.snapshots - first_snapshot}")
    return screen


def read_inbox(screen, started=None):
    parsed_at = time.perf_counter()
    area, rows = inbox_page(screen)
    real = [row for row, system in rows if not system]
    timing = elapsed(started) if started is not None else f"XML reutilizado, análisis {elapsed(parsed_at)}"
    event("BANDEJA", f"{len(rows)} filas visibles | {len(real)} conversaciones | {timing}")
    return area, real


def page_key(rows):
    # Ignore changes to preview, time and unread badges.
    return tuple((normalize(row.alias), row.bounds) for row in rows)


def traverse(driver, keyboard, journal, text, args, results):
    seen = journal.examined_names()
    if args.number > 0 and len(seen) >= args.number:
        return "límite de conversaciones del lote alcanzado"
    event("BANDEJA", "Abriendo Mensaje...")
    started = time.perf_counter()
    deadline = started + MAX_RUN_SECONDS
    screen = driver.prepare_inbox()
    area, rows = read_inbox(screen, started)
    scrolls = stable = 0
    toward_top = args.number == -1 or bool(getattr(args, "resume", None))
    while True:
        if max(time.perf_counter(), results.next_send_at) >= deadline:
            raise ReviewRequired("inbox_time_limit")
        aliases = Counter(normalize(row.alias) for row in rows)
        candidates = ((row, point) for row in rows if digest_text(normalize(row.alias)) not in seen
                      and (point := inbox_row_point(area, row)) is not None)
        if candidate := next(candidates, None):
            row, point = candidate
            name_hash = digest_text(normalize(row.alias))
            position = len(seen) + 1
            journal.start(name_hash, position)
            seen.add(name_hash)
            results.examined += 1
            examined_at = time.perf_counter()
            try:
                if returned := visit(driver, keyboard, journal, row, point, aliases, text, args, results, position):
                    area, rows = read_inbox(returned)
            finally:
                results.recipient_seconds += time.perf_counter() - examined_at
            if results.adb_errors >= MAX_CONSECUTIVE_ADB_ERRORS:
                raise ReviewRequired("errores consecutivos de ADB o del teclado")
            if position == args.number:
                return "límite de conversaciones alcanzado"
            stable = 0
            continue

        if getattr(args, "inbox_only", False):
            return "fin de las filas visibles"
        if scrolls == MAX_SCROLLS:
            raise ReviewRequired("inbox_scroll_limit")
        before = page_key(rows)
        event("SCROLL", "Buscando hacia el inicio..." if toward_top else "Buscando más conversaciones...")
        started = time.perf_counter()
        screen = driver.scroll_page(area, toward_top=toward_top)
        event("SCROLL COMPLETADO", elapsed(started))
        area, rows = read_inbox(screen)
        scrolls += 1
        stable = stable + 1 if before == page_key(rows) else 0
        if stable == 2:
            if not toward_top:
                return "fin de la bandeja"
            toward_top = False
            stable = 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Envío ligero desde la bandeja de MeYo")
    parser.add_argument("--number", type=int, required=True)
    parser.add_argument("--delay-min", type=float, default=1)
    parser.add_argument("--delay-max", type=float, default=2)
    parser.add_argument("--activate-adb-keyboard", action="store_true")
    parser.add_argument("--fast-focus", action="store_true")
    parser.add_argument("--resume", type=UUID)
    parser.add_argument("--inbox-only", action="store_true")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.inbox_only and (args.apply or args.resume):
        parser.error("--inbox-only solo se admite en una prueba en seco nueva.")
    text = os.environ.get("AUTODATE_OUTREACH_MESSAGE", "")
    if not text.strip() or (args.number != -1 and args.number < 1):
        parser.error("Indica un mensaje y un número de conversaciones positivo o -1.")
    if not (math.isfinite(args.delay_min) and math.isfinite(args.delay_max) and 0 <= args.delay_min <= args.delay_max):
        parser.error("Las pausas deben ser finitas y cumplir 0 <= delay-min <= delay-max.")

    started = time.perf_counter()
    results = Results()
    timings = AdbTimings()
    limit = args.number if args.number > 0 else "todas"
    mode = "APPLY" if args.apply else "DRY-RUN"
    message_hash = digest_text(text)
    event("INICIO", f"{mode} | Máximo de conversaciones: {limit} | Huella del mensaje: {message_hash[:12]}")
    reason = "detenido para revisión"
    try:
        with ExitStack() as stack:
            stack.enter_context(device_lock(ZTE_SERIAL))
            JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            journal_db = stack.enter_context(closing(sqlite3.connect(JOURNAL_PATH, timeout=0.5)))
            journal = RunJournal(journal_db, message_hash, args.number, args.apply, resume=args.resume)
            event("LOTE", f"{journal.id} | Examinadas antes de iniciar: {len(journal.examined_names())}")
            transport = AdbTransport(ZTE_SERIAL, retries=0, logger=timings)
            keyboard = LiteKeyboard(transport.command)
            if args.apply:
                event("TECLADO", "Verificando ADB Keyboard...")
                stack.enter_context(keyboard.session(activate=args.activate_adb_keyboard, require_ready=True))
            reason = traverse(LiteDriver(transport), keyboard, journal, text, args, results)
        return 2 if results.reviews else 0
    except KeyboardInterrupt:
        reason = "interrumpido por el operador"
        return 130
    except ReviewRequired as exc:
        event("REVISIÓN", exc.code)
    except WorkerError as exc:
        event("ERROR", exc.code)
    except sqlite3.Error as exc:
        event("ERROR", f"SQLite del lote no disponible ({getattr(exc, 'sqlite_errorname', 'SQLite')})")
    except OSError as exc:
        event("ERROR", f"fallo local ({exc.strerror or type(exc).__name__})")
    finally:
        timings.report()
        for title, causes in (("REVISIONES", results.reviews_by_cause), ("OMISIONES", results.skips_by_cause)):
            if causes:
                event(title, " | ".join(f"{cause}: {count}" for cause, count in causes.most_common()))
        average = results.recipient_seconds / results.examined if results.examined else 0
        event("FIN", f"{reason} | Examinadas en esta ejecución: {results.examined} | Acciones IME sin verificar: {results.sent}"
              f" | Omitidas: {results.skipped} | En revisión: {results.reviews} | Simuladas: {results.simulated}"
              f" | Chats recorridos: {results.opened} | Recuperaciones: {results.recoveries}"
              f" | Transcurrido: {elapsed(started)} | Promedio por conversación: {average:.1f} s")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
