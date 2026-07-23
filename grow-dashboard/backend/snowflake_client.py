"""Snowflake connection + query helpers.

Replaces the Cowork artifact's `runSF()` (which called
`window.cowork.callMcpTool(SF_TOOL, {statement})`) with a direct, key-pair
authenticated connection using a dedicated read-only service account.

All queries use bound parameters ("?" placeholders, qmark paramstyle) instead
of the artifact's string interpolation — required now that user-supplied
values (analytical_name, days) cross a real network boundary, and also
because several of the ported queries contain literal "%" characters (ILIKE
'%...%') that would collide with the connector's default pyformat paramstyle.
"""

from pathlib import Path
from typing import Any, Sequence

import snowflake.connector
from cryptography.hazmat.primitives import serialization

from .config import Settings, get_settings

snowflake.connector.paramstyle = "qmark"


def _load_private_key(settings: Settings) -> bytes:
    if settings.snowflake_private_key_path:
        pem_bytes = Path(settings.snowflake_private_key_path).read_bytes()
    elif settings.snowflake_private_key_pem:
        pem_bytes = settings.snowflake_private_key_pem.encode()
    else:
        raise RuntimeError(
            "Set SNOWFLAKE_PRIVATE_KEY_PATH or SNOWFLAKE_PRIVATE_KEY_PEM in .env"
        )

    passphrase = (
        settings.snowflake_private_key_passphrase.encode()
        if settings.snowflake_private_key_passphrase
        else None
    )
    private_key = serialization.load_pem_private_key(pem_bytes, password=passphrase)
    return private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_connection(
    settings: Settings | None = None,
) -> snowflake.connector.SnowflakeConnection:
    settings = settings or get_settings()
    return snowflake.connector.connect(
        account=settings.snowflake_account,
        user=settings.snowflake_user,
        role=settings.snowflake_role,
        warehouse=settings.snowflake_warehouse,
        database=settings.snowflake_database,
        private_key=_load_private_key(settings),
    )


def run_query(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    """Run a read-only, bound-parameter query; return rows as a list of dicts.

    Opens a fresh connection per call — call volume is low (one scheduled
    refresh per interval, plus occasional on-demand DQ checks), so connection
    pooling isn't worth the added complexity here.
    """
    conn = get_connection()
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        cur.execute(sql, params or ())
        return cur.fetchall()
    finally:
        conn.close()
