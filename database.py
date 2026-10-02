"""
database.py — SQLAlchemy async models + session factory.
Designed for easy migration to PostgreSQL: just swap DATABASE_URL.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, func, select
)
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config import DATABASE_URL, DATA_DIR


# ── Engine & session ──────────────────────────────────────────────────────────

DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


# ── Base ──────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Models ────────────────────────────────────────────────────────────────────

class Bot(Base):
    __tablename__ = "bots"

    id:            Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    name:          Mapped[str]  = mapped_column(String(100), nullable=False)
    slug:          Mapped[str]  = mapped_column(String(120), unique=True, nullable=False)
    directory:     Mapped[str]  = mapped_column(String(500), nullable=False)
    entry_file:    Mapped[str | None] = mapped_column(String(200))
    state:         Mapped[str]  = mapped_column(String(30), default="stopped")
    pid:           Mapped[int | None] = mapped_column(Integer)
    auto_start:    Mapped[bool] = mapped_column(Boolean, default=True)
    restart_count: Mapped[int]  = mapped_column(Integer, default=0)
    last_error:    Mapped[str | None] = mapped_column(Text)
    crash_loop:    Mapped[bool] = mapped_column(Boolean, default=False)
    mode:          Mapped[str]  = mapped_column(String(20), default="polling")  # polling | webhook
    webhook_path:  Mapped[str | None] = mapped_column(String(300))
    cpu_limit:     Mapped[float | None] = mapped_column(Float)
    ram_limit_mb:  Mapped[int | None]   = mapped_column(Integer)
    created_at:    Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at:    Mapped[datetime | None] = mapped_column(DateTime)
    stopped_at:    Mapped[datetime | None] = mapped_column(DateTime)

    env_vars:     Mapped[list[EnvVar]]       = relationship("EnvVar",       back_populates="bot", cascade="all, delete-orphan")
    versions:     Mapped[list[BotVersion]]   = relationship("BotVersion",   back_populates="bot", cascade="all, delete-orphan")
    health_cfg:   Mapped[HealthConfig | None] = relationship("HealthConfig", back_populates="bot", uselist=False, cascade="all, delete-orphan")
    schedules:    Mapped[list[ScheduledTask]] = relationship("ScheduledTask", back_populates="bot", cascade="all, delete-orphan")
    perf_history: Mapped[list[PerfSample]]   = relationship("PerfSample",   back_populates="bot", cascade="all, delete-orphan")
    net_stats:    Mapped[list[NetworkStat]]  = relationship("NetworkStat",  back_populates="bot", cascade="all, delete-orphan")


class EnvVar(Base):
    __tablename__ = "env_vars"

    id:        Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id:    Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"))
    key:       Mapped[str] = mapped_column(String(200), nullable=False)
    value_enc: Mapped[str] = mapped_column(Text, nullable=False)   # Fernet encrypted
    bot:       Mapped[Bot] = relationship("Bot", back_populates="env_vars")


class BotVersion(Base):
    __tablename__ = "bot_versions"

    id:          Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id:      Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"))
    version_num: Mapped[int] = mapped_column(Integer, nullable=False)
    backup_path: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    bot:         Mapped[Bot] = relationship("Bot", back_populates="versions")


class HealthConfig(Base):
    __tablename__ = "health_configs"

    id:                Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id:            Mapped[int]  = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), unique=True)
    check_type:        Mapped[str]  = mapped_column(String(30), default="telegram_ping")
    endpoint:          Mapped[str | None] = mapped_column(String(500))
    interval_minutes:  Mapped[int]  = mapped_column(Integer, default=5)
    timeout_seconds:   Mapped[int]  = mapped_column(Integer, default=10)
    failure_threshold: Mapped[int]  = mapped_column(Integer, default=3)
    enabled:           Mapped[bool] = mapped_column(Boolean, default=True)
    last_check:        Mapped[datetime | None] = mapped_column(DateTime)
    last_status:       Mapped[str | None] = mapped_column(String(30))
    bot:               Mapped[Bot]  = relationship("Bot", back_populates="health_cfg")


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id:          Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id:      Mapped[int | None] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"))
    action:      Mapped[str]  = mapped_column(String(50), nullable=False)
    cron_expr:   Mapped[str]  = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))
    enabled:     Mapped[bool] = mapped_column(Boolean, default=True)
    last_run:    Mapped[datetime | None] = mapped_column(DateTime)
    created_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    bot:         Mapped[Bot | None] = relationship("Bot", back_populates="schedules")


class TokenVault(Base):
    __tablename__ = "token_vault"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label:      Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value_enc:  Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action:     Mapped[str] = mapped_column(String(200), nullable=False)
    detail:     Mapped[str | None] = mapped_column(Text)
    bot_name:   Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PerfSample(Base):
    __tablename__ = "perf_history"

    id:         Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id:     Mapped[int]   = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"))
    cpu_pct:    Mapped[float] = mapped_column(Float, default=0.0)
    ram_mb:     Mapped[float] = mapped_column(Float, default=0.0)
    sampled_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    bot:        Mapped[Bot]   = relationship("Bot", back_populates="perf_history")


class NetworkStat(Base):
    __tablename__ = "network_stats"

    id:          Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id:      Mapped[int]   = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"))
    bytes_sent:  Mapped[int]   = mapped_column(Integer, default=0)
    bytes_recv:  Mapped[int]   = mapped_column(Integer, default=0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    bot:         Mapped[Bot]   = relationship("Bot", back_populates="net_stats")


class NotificationConfig(Base):
    __tablename__ = "notification_config"

    id:              Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    on_crash:        Mapped[bool] = mapped_column(Boolean, default=True)
    on_crash_loop:   Mapped[bool] = mapped_column(Boolean, default=True)
    on_start:        Mapped[bool] = mapped_column(Boolean, default=False)
    on_stop:         Mapped[bool] = mapped_column(Boolean, default=False)
    on_high_cpu:     Mapped[bool] = mapped_column(Boolean, default=True)
    on_high_ram:     Mapped[bool] = mapped_column(Boolean, default=True)
    on_low_disk:     Mapped[bool] = mapped_column(Boolean, default=True)
    on_reboot_restore: Mapped[bool] = mapped_column(Boolean, default=True)
    quiet_hours_start: Mapped[int] = mapped_column(Integer, default=2)
    quiet_hours_end:   Mapped[int] = mapped_column(Integer, default=7)


# ── Init ──────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # ensure default notification config exists
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(NotificationConfig))
        if not result.scalars().first():
            session.add(NotificationConfig())
            await session.commit()
