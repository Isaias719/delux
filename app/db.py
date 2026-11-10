"""Acceso a la base de datos SQLite (archivo delux.db)."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("DELUX_DB", BASE_DIR / "delux.db"))
UPLOAD_DIR = Path(os.getenv("DELUX_UPLOADS", BASE_DIR / "uploads"))


@contextmanager
def db():
    """Abre una conexión, confirma al terminar y la cierra."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre      TEXT NOT NULL,
                descripcion TEXT NOT NULL DEFAULT '',
                foto        TEXT,
                largo_cm    REAL NOT NULL,
                ancho_cm    REAL NOT NULL,
                grueso_cm   REAL NOT NULL,
                precio_m2   REAL NOT NULL,
                creado_en   TEXT NOT NULL
            );

            -- La cotización guarda una copia del nombre y del precio, así el
            -- historial no se pierde aunque la placa se elimine o cambie de precio.
            CREATE TABLE IF NOT EXISTS quotes (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id       INTEGER NOT NULL,
                producto_nombre  TEXT NOT NULL,
                cliente_nombre   TEXT NOT NULL,
                cliente_contacto TEXT NOT NULL,
                metros_cuadrados REAL NOT NULL,
                precio_m2        REAL NOT NULL,
                total            REAL NOT NULL,
                mensaje          TEXT NOT NULL DEFAULT '',
                estado           TEXT NOT NULL DEFAULT 'PENDIENTE',
                creado_en        TEXT NOT NULL
            );
            """
        )
