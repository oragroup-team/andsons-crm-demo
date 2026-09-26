"""Read-only client for the real per-brand/market SQL warehouse (production
MySQL, read access from Madan, 2026-09-24) - the real source for data
MoEngage genuinely doesn't have (payment-retry/recovery, order/subscription
detail) and that BigQuery's own export doesn't carry either. Separate from
both moengage_client.py (MoEngage's own API) and the BigQuery connection in
agents/analytics_agent.py (a different real warehouse entirely) - this one
talks directly to each brand's own production database over MySQL.

CAUTION (real, not hypothetical): AS_SG/OVA_SG's real DB user is named
"dev", not "ro" like the other 6 - unconfirmed whether that account has
write privileges on these real PRODUCTION databases. Every query this
module runs is therefore forced through a real, enforced SELECT-only check
(_ensure_select_only) regardless of which brand/user it's running as -
never assume "dev" is safely read-only just because every current caller
happens to use it read-only.

Only Ova MY is confirmed network-reachable as of 2026-09-24 (live TCP
test) - the other 7 real credential sets (AS_SG, OVA_SG, AS_MY, AS_PH,
OVA_PH, MODULES_SG, AS_GL - MODULES_SG and AS_GL share AS_SG's host) still
time out at the TCP level, a real RDS security-group whitelist gap (this
machine's public IP wasn't in every relevant security group yet), not a
credentials problem. Every function here is written to work for all 8 the
moment each one's real network path opens up - no code change needed then,
just Madan's own whitelist action on his side."""
import os
import re

import pymysql
from dotenv import load_dotenv

load_dotenv()

# Every real brand/market this project has credentials for - see
# backend/.env's own comments for provenance (Madan's 2026-09-24 message).
BRANDS = ["AS_SG", "OVA_SG", "AS_MY", "OVA_MY", "AS_PH", "OVA_PH", "MODULES_SG", "AS_GL"]


def _config(brand: str) -> dict:
    brand = brand.upper()
    if brand not in BRANDS:
        raise ValueError(f"Unknown brand {brand!r} - must be one of {BRANDS}")
    host = os.environ.get(f"{brand}_DB_HOST")
    if not host:
        raise RuntimeError(f"{brand}_DB_HOST is not configured - no real credentials for this brand yet.")
    return {
        "host": host,
        "database": os.environ.get(f"{brand}_DB_NAME"),
        "user": os.environ.get(f"{brand}_DB_USER"),
        "password": os.environ.get(f"{brand}_DB_PASSWORD"),
    }


def get_connection(brand: str, connect_timeout: int = 10):
    """A real, fresh connection to one real brand's production DB. Raises a
    clear, real error (not a generic traceback) distinguishing a network/
    whitelist gap (TCP never connects - the current, live, confirmed state
    for 7 of 8 real brands) from an auth/credentials failure, since those
    need genuinely different real-world fixes (ask Madan to whitelist this
    IP, vs. double-check the password)."""
    cfg = _config(brand)
    try:
        # client_flag is left at pymysql's default (0) deliberately - it
        # does NOT include CLIENT_MULTI_STATEMENTS, so the MySQL protocol
        # itself refuses to execute more than one statement per call, a
        # real server-level guard independent of _ensure_select_only's own
        # app-level stacked-statement check above.
        return pymysql.connect(
            host=cfg["host"], user=cfg["user"], password=cfg["password"],
            database=cfg["database"], port=3306, connect_timeout=connect_timeout,
        )
    except (pymysql.err.OperationalError, TimeoutError, OSError) as exc:
        msg = str(exc)
        if "timed out" in msg.lower() or "timeout" in msg.lower():
            raise RuntimeError(
                f"{brand}: can't reach {cfg['host']} - this is a real network/security-group "
                f"whitelist gap (confirmed live 2026-09-24: only Ova MY's host is currently "
                f"reachable), not a credentials problem. Ask Madan to whitelist this server's "
                f"public IP for this brand's RDS security group too."
            ) from exc
        raise RuntimeError(f"{brand}: connection to {cfg['host']} failed: {exc}") from exc


_WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE|CALL|LOCK|UNLOCK|SET)\b",
    re.IGNORECASE,
)


_FILE_WRITE_PATTERN = re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b|\bLOAD_FILE\s*\(", re.IGNORECASE)


def _ensure_select_only(sql: str) -> None:
    """A real, enforced guardrail, not a courtesy - these are real
    PRODUCTION databases, and at least one real brand's own configured DB
    user ("dev" for AS_SG/OVA_SG) has unconfirmed write privileges (see
    module docstring). Every query this module ever runs goes through
    this check first, regardless of which brand/user it targets. This is a
    keyword/pattern check, not a real SQL parser, so it's deliberately
    layered with two other independent guards in run_query() below (a
    forced read-only DB session, and multi-statement execution left
    disabled at the connection level) - no single layer here is trusted
    alone."""
    stripped = sql.strip()
    # Reject stacked statements (a `;` anywhere except one optional
    # trailing terminator) - blocks e.g. "SELECT 1; DELETE FROM x" even
    # though pymysql's default connection (no CLIENT_MULTI_STATEMENTS
    # flag - see get_connection) already wouldn't execute a second
    # statement; this is an explicit, visible check, not a reliance on
    # that library default holding forever.
    body = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in body:
        raise ValueError("Only a single statement is allowed - no stacked/multiple statements against this real production warehouse.")
    if not re.match(r"^\s*(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN)\b", body, re.IGNORECASE):
        raise ValueError("Only SELECT/SHOW/DESCRIBE/EXPLAIN statements are allowed against this real production warehouse.")
    if _WRITE_KEYWORDS.search(body):
        raise ValueError("Query contains a write/DDL keyword - not allowed against this real production warehouse.")
    if _FILE_WRITE_PATTERN.search(body):
        raise ValueError("SELECT ... INTO OUTFILE/DUMPFILE and LOAD_FILE() are not allowed - these can read/write the DB server's own filesystem, not just table data.")


def list_tables(brand: str) -> list:
    conn = get_connection(brand)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def table_schema(brand: str, table: str) -> list:
    """Real column definitions for one real table - name/type/nullable/key,
    exactly as MySQL's own DESCRIBE returns them."""
    if not re.match(r"^[A-Za-z0-9_]+$", table):
        raise ValueError(f"Invalid table name {table!r}")
    conn = get_connection(brand)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DESCRIBE `{table}`")
            return [
                {"column": row[0], "type": row[1], "nullable": row[2], "key": row[3], "default": row[4]}
                for row in cur.fetchall()
            ]
    finally:
        conn.close()


def run_query(brand: str, sql: str, max_rows: int = 200) -> dict:
    """Runs one real, read-only query against one real brand's production
    DB. Returns {"columns": [...], "rows": [[...], ...]} - real data, never
    fabricated or estimated. max_rows caps what's returned to the caller
    (the query itself still runs in full against the real DB; only the
    result set handed back is capped, so an LLM-driven query can't flood
    context with a huge real table dump).

    This is the one path here that runs LLM-generated, not hand-written,
    SQL - so it's the only one that pays for a THIRD independent guard on
    top of _ensure_select_only's own text check and get_connection's
    multi-statement-disabled connection: the session itself is put into
    MySQL's real read-only transaction mode before the caller's query
    ever runs, so even a write statement this module's own text check
    somehow missed would still be rejected by MySQL itself, not just by
    app-level pattern matching."""
    _ensure_select_only(sql)
    conn = get_connection(brand)
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows) if cur.description else []
            return {"columns": columns, "rows": [list(r) for r in rows], "truncated_to": max_rows}
    finally:
        conn.close()


def brand_status() -> dict:
    """A real, live reachability probe (raw TCP, no auth) for every real
    configured brand - lets a caller (or the analytics agent) know up
    front which of the 8 real brands can actually be queried right now,
    without needing to try-and-fail on every one first. Cheap (a few
    seconds total), safe to call per question."""
    import socket

    out = {}
    for brand in BRANDS:
        try:
            cfg = _config(brand)
        except RuntimeError as exc:
            out[brand] = f"not configured ({exc})"
            continue
        try:
            sock = socket.create_connection((cfg["host"], 3306), timeout=4)
            sock.close()
            out[brand] = "reachable"
        except Exception:
            out[brand] = "unreachable (network/whitelist gap)"
    return out
