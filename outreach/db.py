import contextlib
import psycopg
from psycopg.rows import dict_row
from outreach.config import DATABASE_DSN


@contextlib.contextmanager
def get_conn():
    conn = psycopg.connect(DATABASE_DSN, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_migration(path: str):
    with open(path) as f:
        sql = f.read()
    with get_conn() as conn:
        conn.execute(sql)
