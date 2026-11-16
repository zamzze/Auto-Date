from uuid import uuid4

from .domain import ReviewRequired, utc_now


class RunJournal:
    def __init__(self, db, message_hash, limit, apply, *, resume=None):
        self.db = db
        self.id = str(resume or uuid4())
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS lite_batches (
                id TEXT PRIMARY KEY, message_hash TEXT NOT NULL, exam_limit INTEGER NOT NULL,
                apply INTEGER NOT NULL, started_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lite_visits (
                batch_id TEXT NOT NULL, name_hash TEXT NOT NULL, position INTEGER NOT NULL,
                recipient_ref TEXT, confidence TEXT NOT NULL, status TEXT NOT NULL,
                reason TEXT NOT NULL, updated_at TEXT NOT NULL, ime_attempted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (batch_id, name_hash),
                FOREIGN KEY (batch_id) REFERENCES lite_batches(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS lite_one_attempt_per_recipient
            ON lite_visits(batch_id, recipient_ref) WHERE ime_attempted=1;
        """)
        if resume:
            row = db.execute("SELECT message_hash, exam_limit, apply FROM lite_batches WHERE id=?", (self.id,)).fetchone()
            if row != (message_hash, limit, int(apply)):
                raise ReviewRequired("lote inexistente o mensaje, límite o modo distintos")
        else:
            with db:
                db.execute("INSERT INTO lite_batches VALUES (?, ?, ?, ?, ?)",
                           (self.id, message_hash, limit, int(apply), utc_now()))

    def examined_names(self):
        return {row[0] for row in self.db.execute("SELECT name_hash FROM lite_visits WHERE batch_id=?", (self.id,))}

    def start(self, name_hash, position):
        with self.db:
            self.db.execute("""INSERT INTO lite_visits
                (batch_id, name_hash, position, confidence, status, reason, updated_at)
                VALUES (?, ?, ?, 'nombre_visible', 'REVISIÓN', 'interrumpido_antes_de_ime', ?)""",
                (self.id, name_hash, position, utc_now()))

    def bind(self, name_hash, reference, confidence):
        with self.db:
            self.db.execute("UPDATE lite_visits SET recipient_ref=?, confidence=? WHERE batch_id=? AND name_hash=?",
                            (reference, confidence, self.id, name_hash))

    def attempted(self, reference):
        return self.db.execute("SELECT 1 FROM lite_visits WHERE batch_id=? AND recipient_ref=? AND ime_attempted=1",
                               (self.id, reference)).fetchone() is not None

    def reserve_ime(self, name_hash):
        with self.db:
            result = self.db.execute("""UPDATE lite_visits SET ime_attempted=1, reason='ime_resultado_incierto', updated_at=?
                WHERE batch_id=? AND name_hash=? AND recipient_ref IS NOT NULL AND ime_attempted=0""",
                (utc_now(), self.id, name_hash))
            if result.rowcount != 1:
                raise ReviewRequired("reserva_ime_no_verificada")

    def finish(self, name_hash, status, reason=""):
        with self.db:
            self.db.execute("UPDATE lite_visits SET status=?, reason=?, updated_at=? WHERE batch_id=? AND name_hash=?",
                            (status, reason, utc_now(), self.id, name_hash))
