import psycopg2
from mm import config


def connect():
    return psycopg2.connect(config.SOURCE_DSN)


def readonly_connect():
    conn = connect()
    conn.set_session(readonly=True, autocommit=False)
    return conn
