from __future__ import annotations

import os

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


# STREAMFORGE_DB_POOL_HEADROOM_V32:
# The Main service handles Panel/API, Remote Node polling and HLS proxy traffic
# in the same process. SQLAlchemy's default file-SQLite QueuePool (5 + 10
# overflow) is too small when several bounded network calls overlap. Keep a
# larger bounded pool as safety headroom; hot paths also release their read
# transaction before waiting on remote I/O (see V32 markers in main/node_manager).
_engine_kwargs = {
    "pool_pre_ping": True,
}
if settings.database_url.startswith("sqlite"):
    # STREAMFORGE_PUBLIC_SQLITE_POOL_ISOLATION_V62:
    # The control plane keeps its historical headroom. Each Public Gunicorn
    # worker gets a deliberately small pool so N workers cannot multiply the
    # old 20+40 connection allowance into hundreds/thousands of SQLite handles.
    # Public hot state is Redis-backed; SQLite remains the persistent catalogue.
    process_role = str(os.getenv("STREAMFORGE_PROCESS_ROLE", "control") or "control").strip().lower()
    if process_role == "public":
        _engine_kwargs.update({
            "connect_args": {"check_same_thread": False},
            "pool_size": 3,
            "max_overflow": 3,
            "pool_timeout": 3,
            "pool_recycle": 300,
        })
    else:
        _engine_kwargs.update({
            "connect_args": {"check_same_thread": False},
            "pool_size": 20,
            "max_overflow": 40,
            "pool_timeout": 10,
            "pool_recycle": 300,
        })

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        # STREAMFORGE_SQLITE_CONNECTION_LIGHTWEIGHT_V34:
        # WAL is a database-level persistent setting; renegotiating
        # journal_mode on every newly-created pooled connection can require a
        # lock exactly when the Main web is under load. Set only per-connection
        # pragmas here; ensure_runtime_schema() enables WAL once per service
        # startup.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
        # STREAMFORGE_REQUEST_DIRTY_COMMIT_V69:
        # Commit request-local playback grant and audit-log writes once after a
        # successful request.  This keeps client logging on the same SQLite
        # session without losing rows when Session.close() rolls back.
        playback_dirty = bool(db.info.pop("streamforge_playback_grants_dirty", False))
        audit_dirty = bool(db.info.pop("streamforge_audit_log_dirty", False))
        if playback_dirty or audit_dirty:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ensure_runtime_schema() -> None:
    """Apply additive, idempotent schema upgrades for packaged releases."""
    if settings.database_url.startswith("sqlite"):
        # STREAMFORGE_SQLITE_WAL_ONCE_V34: database-level WAL mode is enabled
        # once, not on every QueuePool connection checkout/create.
        with engine.begin() as connection:
            connection.execute(text("PRAGMA journal_mode=WAL"))
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    dialect = engine.dialect.name
    statements: list[str] = []

    if "admin_users" in table_names:
        admin_columns = {column["name"] for column in inspector.get_columns("admin_users")}
        if "role_id" not in admin_columns:
            statements.append("ALTER TABLE admin_users ADD COLUMN role_id INTEGER REFERENCES roles(id) ON DELETE SET NULL")
        if "main_panel_access" not in admin_columns:
            statements.append(
                "ALTER TABLE admin_users ADD COLUMN main_panel_access BOOLEAN NOT NULL DEFAULT 1"
                if dialect == "sqlite" else
                "ALTER TABLE admin_users ADD COLUMN main_panel_access BOOLEAN NOT NULL DEFAULT TRUE"
            )

    if "nodes" in table_names:
        node_columns = {column["name"] for column in inspector.get_columns("nodes")}
        if "agent_version" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN agent_version VARCHAR(64)")
        if "client_prefixes" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN client_prefixes TEXT")
        if "api_urls" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN api_urls TEXT")
        if "playlist_urls" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN playlist_urls TEXT")
        if "access_slug" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN access_slug VARCHAR(120)")
        if "playlist_access_slug" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN playlist_access_slug VARCHAR(120)")
        if "dns_name" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN dns_name VARCHAR(255)")
        if "dns_scheme" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN dns_scheme VARCHAR(10) NOT NULL DEFAULT 'http'")
        if "dns_only" not in node_columns:
            statements.append(
                "ALTER TABLE nodes ADD COLUMN dns_only BOOLEAN NOT NULL DEFAULT 0"
                if dialect == "sqlite" else
                "ALTER TABLE nodes ADD COLUMN dns_only BOOLEAN NOT NULL DEFAULT FALSE"
            )
        if "playlist_url" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN playlist_url TEXT")
        if "playlist_dns_only" not in node_columns:
            statements.append(
                "ALTER TABLE nodes ADD COLUMN playlist_dns_only BOOLEAN NOT NULL DEFAULT 0"
                if dialect == "sqlite" else
                "ALTER TABLE nodes ADD COLUMN playlist_dns_only BOOLEAN NOT NULL DEFAULT FALSE"
            )
        if "playlist_port" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN playlist_port INTEGER NOT NULL DEFAULT 80")
        if "local_channel_limit" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN local_channel_limit INTEGER NOT NULL DEFAULT 0")
        if "total_max_connections" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN total_max_connections INTEGER NOT NULL DEFAULT 0")
        if "logo_url" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN logo_url TEXT")
        if "ssh_host" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN ssh_host VARCHAR(255)")
        if "ssh_port" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN ssh_port INTEGER NOT NULL DEFAULT 22")
        if "ssh_user" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN ssh_user VARCHAR(120)")
        if "ssh_password_enc" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN ssh_password_enc TEXT")
        if "agent_port" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN agent_port INTEGER NOT NULL DEFAULT 80")
        if "panel_ip_whitelist" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN panel_ip_whitelist TEXT")
        if "panel_ip_blacklist" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN panel_ip_blacklist TEXT")
        if "panel_asn_whitelist" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN panel_asn_whitelist TEXT")
        if "panel_asn_blacklist" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN panel_asn_blacklist TEXT")
        if "ip_whitelist" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN ip_whitelist TEXT")
        if "ip_blacklist" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN ip_blacklist TEXT")
        if "asn_whitelist" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN asn_whitelist TEXT")
        if "asn_blacklist" not in node_columns:
            statements.append("ALTER TABLE nodes ADD COLUMN asn_blacklist TEXT")

    if "channel_categories" in table_names:
        category_columns = {column["name"] for column in inspector.get_columns("channel_categories")}
        if "sort_order" not in category_columns:
            statements.append("ALTER TABLE channel_categories ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 100")

    if "playlist_profiles" in table_names:
        playlist_columns = {column["name"] for column in inspector.get_columns("playlist_profiles")}
        if "category_order" not in playlist_columns:
            statements.append("ALTER TABLE playlist_profiles ADD COLUMN category_order TEXT NOT NULL DEFAULT '[]'")
        if "all_enabled_channels" not in playlist_columns:
            statements.append(
                "ALTER TABLE playlist_profiles ADD COLUMN all_enabled_channels BOOLEAN NOT NULL DEFAULT 0"
                if dialect == "sqlite"
                else "ALTER TABLE playlist_profiles ADD COLUMN all_enabled_channels BOOLEAN NOT NULL DEFAULT FALSE"
            )

    if "stream_users" in table_names:
        user_columns = {column["name"] for column in inspector.get_columns("stream_users")}
        if "xtream_password_enc" not in user_columns:
            statements.append("ALTER TABLE stream_users ADD COLUMN xtream_password_enc TEXT")
        if "load_balance_enabled" not in user_columns:
            statements.append(
                "ALTER TABLE stream_users ADD COLUMN load_balance_enabled BOOLEAN NOT NULL DEFAULT 1"
                if dialect == "sqlite"
                else "ALTER TABLE stream_users ADD COLUMN load_balance_enabled BOOLEAN NOT NULL DEFAULT TRUE"
            )
        if "delivery_mode" not in user_columns:
            statements.append("ALTER TABLE stream_users ADD COLUMN delivery_mode VARCHAR(30) NOT NULL DEFAULT 'central'")
        if "direct_node_id" not in user_columns:
            statements.append("ALTER TABLE stream_users ADD COLUMN direct_node_id INTEGER REFERENCES nodes(id) ON DELETE SET NULL")
        if "playlist_order" not in user_columns:
            statements.append("ALTER TABLE stream_users ADD COLUMN playlist_order TEXT NOT NULL DEFAULT '[]'")
        if "playlist_id" not in user_columns:
            statements.append("ALTER TABLE stream_users ADD COLUMN playlist_id INTEGER REFERENCES playlist_profiles(id) ON DELETE SET NULL")
        if "xtream_username" not in user_columns:
            statements.append("ALTER TABLE stream_users ADD COLUMN xtream_username VARCHAR(120)")
        if "xtream_password_hash" not in user_columns:
            statements.append("ALTER TABLE stream_users ADD COLUMN xtream_password_hash VARCHAR(255)")
        if "user_type" not in user_columns:
            statements.append("ALTER TABLE stream_users ADD COLUMN user_type VARCHAR(20) NOT NULL DEFAULT 'viewer'")
        if "restream_allowed_ips" not in user_columns:
            statements.append("ALTER TABLE stream_users ADD COLUMN restream_allowed_ips TEXT")

    if "node_stream_users" in table_names:
        node_user_columns = {column["name"] for column in inspector.get_columns("node_stream_users")}
        if "playlist_order" not in node_user_columns:
            statements.append("ALTER TABLE node_stream_users ADD COLUMN playlist_order TEXT NOT NULL DEFAULT '[]'")

    if "channels" in table_names and "channel_categories" in table_names and "channel_category_links" not in table_names:
        statements.append(
            "CREATE TABLE channel_category_links (channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE, category_id INTEGER NOT NULL REFERENCES channel_categories(id) ON DELETE CASCADE, position INTEGER NOT NULL DEFAULT 100, PRIMARY KEY (channel_id, category_id))"
        )

    if "channels" in table_names:
        columns = {column["name"] for column in inspector.get_columns("channels")}
        if "auto_restart" not in columns:
            statements.append(
                "ALTER TABLE channels ADD COLUMN auto_restart BOOLEAN NOT NULL DEFAULT 1"
                if dialect == "sqlite"
                else "ALTER TABLE channels ADD COLUMN auto_restart BOOLEAN NOT NULL DEFAULT TRUE"
            )
        if "desired_running" not in columns:
            statements.append(
                "ALTER TABLE channels ADD COLUMN desired_running BOOLEAN NOT NULL DEFAULT 0"
                if dialect == "sqlite"
                else "ALTER TABLE channels ADD COLUMN desired_running BOOLEAN NOT NULL DEFAULT FALSE"
            )
        if "category_id" not in columns:
            statements.append("ALTER TABLE channels ADD COLUMN category_id INTEGER REFERENCES channel_categories(id) ON DELETE SET NULL")
        if "sort_order" not in columns:
            statements.append("ALTER TABLE channels ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 100")
        if "logo_url" not in columns:
            statements.append("ALTER TABLE channels ADD COLUMN logo_url TEXT")
        if "node_id" not in columns:
            statements.append("ALTER TABLE channels ADD COLUMN node_id INTEGER REFERENCES nodes(id) ON DELETE SET NULL")
        if "remote_input_mode" not in columns:
            statements.append("ALTER TABLE channels ADD COLUMN remote_input_mode VARCHAR(30) NOT NULL DEFAULT 'source'")
        if "backup_inputs" not in columns:
            statements.append("ALTER TABLE channels ADD COLUMN backup_inputs TEXT")
        if "source_program_ids" not in columns:
            statements.append("ALTER TABLE channels ADD COLUMN source_program_ids TEXT")
        if "active_input_index" not in columns:
            statements.append("ALTER TABLE channels ADD COLUMN active_input_index INTEGER NOT NULL DEFAULT 0")
        if "failback_enabled" not in columns:
            statements.append(
                "ALTER TABLE channels ADD COLUMN failback_enabled BOOLEAN NOT NULL DEFAULT 1"
                if dialect == "sqlite" else
                "ALTER TABLE channels ADD COLUMN failback_enabled BOOLEAN NOT NULL DEFAULT TRUE"
            )
        if "failback_interval" not in columns:
            statements.append("ALTER TABLE channels ADD COLUMN failback_interval INTEGER NOT NULL DEFAULT 30")

    if "nodes" in table_names:
        node_columns = {column["name"] for column in inspector.get_columns("nodes")}
        if "sync_main_users" not in node_columns:
            statements.append(
                "ALTER TABLE nodes ADD COLUMN sync_main_users BOOLEAN NOT NULL DEFAULT 0"
                if dialect == "sqlite" else
                "ALTER TABLE nodes ADD COLUMN sync_main_users BOOLEAN NOT NULL DEFAULT FALSE"
            )

    if "channel_nodes" in table_names:
        channel_node_columns = {column["name"] for column in inspector.get_columns("channel_nodes")}
        if "input_mode" not in channel_node_columns:
            statements.append("ALTER TABLE channel_nodes ADD COLUMN input_mode VARCHAR(30) NOT NULL DEFAULT 'source'")
        if "video_codec" not in channel_node_columns:
            statements.append("ALTER TABLE channel_nodes ADD COLUMN video_codec VARCHAR(30)")
        if "video_bitrate" not in channel_node_columns:
            statements.append("ALTER TABLE channel_nodes ADD COLUMN video_bitrate VARCHAR(20)")
        if "resolution" not in channel_node_columns:
            statements.append("ALTER TABLE channel_nodes ADD COLUMN resolution VARCHAR(30)")
        if "audio_codec" not in channel_node_columns:
            statements.append("ALTER TABLE channel_nodes ADD COLUMN audio_codec VARCHAR(30)")
        if "audio_bitrate" not in channel_node_columns:
            statements.append("ALTER TABLE channel_nodes ADD COLUMN audio_bitrate VARCHAR(20)")
        if "hls_segment_time" not in channel_node_columns:
            statements.append("ALTER TABLE channel_nodes ADD COLUMN hls_segment_time INTEGER")

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        if "channels" in table_names:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_channels_category_id ON channels(category_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_channel_category_links_category_id ON channel_category_links(category_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_channel_category_links_channel_id ON channel_category_links(channel_id)"))
            if dialect == "sqlite":
                connection.execute(text("INSERT OR IGNORE INTO channel_category_links(channel_id, category_id, position) SELECT id, category_id, 10 FROM channels WHERE category_id IS NOT NULL"))
            else:
                connection.execute(text("INSERT INTO channel_category_links(channel_id, category_id, position) SELECT id, category_id, 10 FROM channels WHERE category_id IS NOT NULL ON CONFLICT(channel_id, category_id) DO NOTHING"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_channels_sort_order ON channels(sort_order)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_channels_node_id ON channels(node_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_channels_desired_running ON channels(desired_running)"))
        if "log_entries" in table_names:
            # STREAMFORGE_STATUS_LOG_INDEX_V34: supports the cached seven-day
            # warning/error channel aggregate used by /status.json.
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_log_entries_status_hot "
                "ON log_entries(created_at, level, channel_id)"
            ))
        if "admin_users" in table_names:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_users_role_id ON admin_users(role_id)"))
        if "stream_users" in table_names:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_stream_users_direct_node_id ON stream_users(direct_node_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_stream_users_playlist_id ON stream_users(playlist_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_stream_users_xtream_username ON stream_users(xtream_username)"))
            if dialect == "sqlite":
                connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_stream_users_xtream_username_lower "
                    "ON stream_users(lower(xtream_username)) "
                    "WHERE xtream_username IS NOT NULL AND xtream_username <> ''"
                ))

        # Base.metadata.create_all creates the association tables. Backfill them
        # from the legacy single-node column without overwriting later choices.
        current_tables = set(inspect(engine).get_table_names())
        if {"channels", "channel_nodes"}.issubset(current_tables):
            connection.execute(text(
                "INSERT OR IGNORE INTO channel_nodes(channel_id,node_id,priority) "
                "SELECT id,node_id,100 FROM channels WHERE node_id IS NOT NULL"
                if dialect == "sqlite" else
                "INSERT INTO channel_nodes(channel_id,node_id,priority) "
                "SELECT id,node_id,100 FROM channels WHERE node_id IS NOT NULL "
                "ON CONFLICT(channel_id,node_id) DO NOTHING"
            ))
