from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


user_channels = Table(
    "user_channels",
    Base.metadata,
    Column("user_id", ForeignKey("stream_users.id", ondelete="CASCADE"), primary_key=True),
    Column("channel_id", ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True),
)


playlist_profile_channels = Table(
    "playlist_profile_channels",
    Base.metadata,
    Column("playlist_id", ForeignKey("playlist_profiles.id", ondelete="CASCADE"), primary_key=True),
    Column("channel_id", ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True),
)



channel_category_links = Table(
    "channel_category_links",
    Base.metadata,
    Column("channel_id", ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("channel_categories.id", ondelete="CASCADE"), primary_key=True),
    Column("position", Integer, nullable=False, default=100),
)

channel_nodes = Table(
    "channel_nodes",
    Base.metadata,
    Column("channel_id", ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True),
    Column("node_id", ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True),
    Column("priority", Integer, nullable=False, default=100),
    Column("input_mode", String(30), nullable=False, default="source"),
    # Nullable values inherit the channel-wide encoding profile.  These fields
    # let the same channel use Copy on one node and H.264/H.265 on another.
    Column("video_codec", String(30), nullable=True),
    Column("video_bitrate", String(20), nullable=True),
    Column("resolution", String(30), nullable=True),
    Column("audio_codec", String(30), nullable=True),
    Column("audio_bitrate", String(20), nullable=True),
    Column("hls_segment_time", Integer, nullable=True),
)


user_nodes = Table(
    "user_nodes",
    Base.metadata,
    Column("user_id", ForeignKey("stream_users.id", ondelete="CASCADE"), primary_key=True),
    Column("node_id", ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True),
)


admin_user_nodes = Table(
    "admin_user_nodes",
    Base.metadata,
    Column("admin_user_id", ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True),
    Column("node_id", ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True),
)


node_user_channels = Table(
    "node_user_channels",
    Base.metadata,
    Column("node_user_id", ForeignKey("node_stream_users.id", ondelete="CASCADE"), primary_key=True),
    Column("channel_id", ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    permissions: Mapped[str] = mapped_column(Text, default="[]")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    panel_users: Mapped[list["AdminUser"]] = relationship(back_populates="role")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    main_panel_access: Mapped[bool] = mapped_column(Boolean, default=True)
    role_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    role: Mapped[Optional[Role]] = relationship(back_populates="panel_users")
    nodes: Mapped[list["Node"]] = relationship(
        secondary=admin_user_nodes, back_populates="panel_users", order_by="Node.node_type, Node.name"
    )


class ChannelCategory(Base):
    __tablename__ = "channel_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    channels: Mapped[list["Channel"]] = relationship(
        back_populates="category",
        order_by="Channel.sort_order, Channel.name",
    )
    linked_channels: Mapped[list["Channel"]] = relationship(
        secondary=channel_category_links,
        back_populates="categories",
        order_by="Channel.sort_order, Channel.name",
        overlaps="category,channels",
    )


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    node_type: Mapped[str] = mapped_column(String(20), default="remote", index=True)
    api_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Newline-separated public/control bases accepted by this node. The first
    # URL is the primary URL used by the Main Panel. Paths are supported so
    # multiple nodes can share one host with different slugs.
    api_urls: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    playlist_urls: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Panel/API and Playlist/App paths are independent so one host can expose
    # separate control and playback routes without coupling their slugs.
    access_slug: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    playlist_access_slug: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    api_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(30), default="unknown")
    # Last Node Agent version reported by the authenticated heartbeat or a
    # successful status/update request. Keeping it in the database lets the
    # Nodes page render the version without waiting for a remote health scan.
    agent_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    client_prefixes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dns_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    dns_scheme: Mapped[str] = mapped_column(String(10), default="http")
    dns_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # Optional public base used by playlist, Android/Xtream API and playback.
    # It may be a different domain or IP from the Node Panel/control URL.
    playlist_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    playlist_dns_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # When enabled, normal users from the Main Panel may authenticate directly
    # against this node. Their catalogue is filtered to channels assigned here.
    sync_main_users: Mapped[bool] = mapped_column(Boolean, default=False)
    # Maximum number of Node-owned channels that may be created from the
    # Remote Node Panel. Zero disables local channel creation in Shared mode.
    local_channel_limit: Mapped[int] = mapped_column(Integer, default=0)
    # Total concurrent viewer sessions accepted by this server. Zero means unlimited.
    total_max_connections: Mapped[int] = mapped_column(Integer, default=0)
    playlist_port: Mapped[int] = mapped_column(Integer, default=80)
    logo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ssh_host: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    ssh_password_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agent_port: Mapped[int] = mapped_column(Integer, default=80)
    # STREAMFORGE_NODE_PANEL_IP_WHITELIST_V98: panel access is independent from playback/catalog access.
    panel_ip_whitelist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # STREAMFORGE_PANEL_FULL_ACCESS_POLICY_V99: Panel browser access has the same
    # four-rule policy shape as playback, but remains independently configured.
    panel_ip_blacklist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    panel_asn_whitelist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    panel_asn_blacklist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_whitelist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_blacklist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    asn_whitelist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    asn_blacklist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    primary_channels: Mapped[list["Channel"]] = relationship(
        back_populates="node", foreign_keys="Channel.node_id", order_by="Channel.name"
    )
    channels: Mapped[list["Channel"]] = relationship(
        secondary=channel_nodes, back_populates="nodes", order_by="Channel.name"
    )
    stream_users: Mapped[list["StreamUser"]] = relationship(
        secondary=user_nodes, back_populates="nodes"
    )
    direct_stream_users: Mapped[list["StreamUser"]] = relationship(
        back_populates="direct_node", foreign_keys="StreamUser.direct_node_id"
    )
    panel_users: Mapped[list["AdminUser"]] = relationship(
        secondary=admin_user_nodes, back_populates="nodes", order_by="AdminUser.username"
    )
    node_stream_users: Mapped[list["NodeStreamUser"]] = relationship(
        back_populates="node", cascade="all, delete-orphan", order_by="NodeStreamUser.name"
    )

    @property
    def is_local(self) -> bool:
        return self.node_type == "local"


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    slug: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    input_url: Mapped[str] = mapped_column(Text)
    backup_inputs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active_input_index: Mapped[int] = mapped_column(Integer, default=0)
    failback_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    failback_interval: Mapped[int] = mapped_column(Integer, default=30)
    logo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    program_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # JSON list aligned with input_url + backup_inputs. Each item is an MPTS program ID or null.
    source_program_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("channel_categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=100, index=True)
    # Legacy/primary node is kept for upgrade compatibility. channel_nodes is authoritative.
    node_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_restart: Mapped[bool] = mapped_column(Boolean, default=True)
    desired_running: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    remote_input_mode: Mapped[str] = mapped_column(String(30), default="source")

    video_codec: Mapped[str] = mapped_column(String(30), default="copy")
    video_bitrate: Mapped[str] = mapped_column(String(20), default="2500k")
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fps: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    preset: Mapped[str] = mapped_column(String(30), default="ultrafast")

    audio_codec: Mapped[str] = mapped_column(String(30), default="copy")
    audio_bitrate: Mapped[str] = mapped_column(String(20), default="128k")

    output_type: Mapped[str] = mapped_column(String(20), default="hls")
    output_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hls_segment_time: Mapped[int] = mapped_column(Integer, default=1)

    status: Mapped[str] = mapped_column(String(30), default="stopped")
    pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    live_bitrate_kbps: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    category: Mapped[Optional[ChannelCategory]] = relationship(back_populates="channels", overlaps="categories,linked_channels")
    categories: Mapped[list[ChannelCategory]] = relationship(
        secondary=channel_category_links,
        back_populates="linked_channels",
        order_by="ChannelCategory.sort_order, ChannelCategory.name",
        overlaps="category,channels",
    )
    node: Mapped[Optional[Node]] = relationship(
        back_populates="primary_channels", foreign_keys=[node_id]
    )
    nodes: Mapped[list[Node]] = relationship(
        secondary=channel_nodes, back_populates="channels", order_by="Node.node_type, Node.name"
    )
    users: Mapped[list["StreamUser"]] = relationship(
        secondary=user_channels, back_populates="channels"
    )
    node_stream_users: Mapped[list["NodeStreamUser"]] = relationship(
        secondary=node_user_channels, back_populates="channels"
    )


class PlaylistProfile(Base):
    __tablename__ = "playlist_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    channel_order: Mapped[str] = mapped_column(Text, default="[]")
    category_order: Mapped[str] = mapped_column(Text, default="[]")
    all_enabled_channels: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    channels: Mapped[list["Channel"]] = relationship(
        secondary=playlist_profile_channels, order_by="Channel.name"
    )
    users: Mapped[list["StreamUser"]] = relationship(back_populates="playlist")


class StreamUser(Base):
    __tablename__ = "stream_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # Optional Xtream-compatible credentials used by Android/TV apps.
    # Passwords are stored as scrypt hashes, never as plaintext.
    xtream_username: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    xtream_password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Encrypted copy is retained only so operators can copy a complete Xtream
    # output URL. Authentication still uses the one-way password hash.
    xtream_password_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    load_balance_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    delivery_mode: Mapped[str] = mapped_column(String(30), default="central")
    direct_node_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    playlist_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("playlist_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    max_connections: Mapped[int] = mapped_column(Integer, default=1)
    user_type: Mapped[str] = mapped_column(String(20), default="viewer", index=True)
    restream_allowed_ips: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    playlist_order: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    channels: Mapped[list[Channel]] = relationship(
        secondary=user_channels, back_populates="users"
    )
    nodes: Mapped[list[Node]] = relationship(
        secondary=user_nodes, back_populates="stream_users", order_by="Node.node_type, Node.name"
    )
    direct_node: Mapped[Optional[Node]] = relationship(
        back_populates="direct_stream_users", foreign_keys=[direct_node_id]
    )
    playlist: Mapped[Optional[PlaylistProfile]] = relationship(back_populates="users")

class NodeStreamUser(Base):
    __tablename__ = "node_stream_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    max_connections: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    playlist_order: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    node: Mapped[Node] = relationship(back_populates="node_stream_users")
    channels: Mapped[list[Channel]] = relationship(
        secondary=node_user_channels, back_populates="node_stream_users", order_by="Channel.name"
    )



class PlaybackGrant(Base):
    __tablename__ = "playback_grants"

    key: Mapped[str] = mapped_column(String(8), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("stream_users.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    ip_address: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(96), default="")
    kind: Mapped[str] = mapped_column(String(20), default="viewer")
    expires_at_epoch: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LogEntry(Base):
    __tablename__ = "log_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(40), default="system", index=True)
    level: Mapped[str] = mapped_column(String(20), default="info", index=True)
    message: Mapped[str] = mapped_column(Text)
    channel_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    node_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    actor: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
