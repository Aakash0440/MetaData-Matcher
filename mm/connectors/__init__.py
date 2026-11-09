"""Pick a connector from a URL (or MM_SOURCE_URL). See config.SOURCE_URL for the accepted forms."""
import os
import re

from mm import config
from mm.connectors.base import MYSQL, POSTGRES, SQLITE, Connector, Dialect  # noqa: F401


def safe_url(url: str) -> str:
    """Hide passwords for display and export."""
    url = re.sub(r"(://[^:/@\s]+):[^@/\s]*@", r"\1:***@", url)
    return re.sub(r"(password\s*=\s*)\S+", r"\1***", url)


def connect(url: str | None = None) -> Connector:
    url = (url or config.SOURCE_URL or "").strip()
    if not url:
        from mm.connectors.postgres import PostgresConnector
        return PostgresConnector(config.SOURCE_DSN)
    low = url.lower()
    if low.startswith("csv://"):
        from mm.connectors.csv_source import CsvConnector
        return CsvConnector(url[6:])
    if os.path.exists(url):
        from mm.connectors.csv_source import CsvConnector
        return CsvConnector(url)
    if low.startswith(("mysql://", "mysql+pymysql://", "mariadb://")):
        from mm.connectors.mysql import MySQLConnector
        return MySQLConnector(url.replace("mariadb://", "mysql://"))
    if low.startswith(("postgresql://", "postgres://")) or "://" not in url:      # URI or key=value DSN
        from mm.connectors.postgres import PostgresConnector
        return PostgresConnector(url)
    raise ValueError(f"unsupported source {safe_url(url)!r}: use postgresql://, mysql://, csv://folder or a CSV path")
