from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class Novel(Base):
    __tablename__ = "novels"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    style: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    characters: Mapped[list["Character"]] = relationship(
        back_populates="novel",
        cascade="all, delete-orphan",
        order_by="Character.created_at",
    )
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="novel",
        cascade="all, delete-orphan",
        order_by="Episode.position",
    )
    jobs: Mapped[list["Job"]] = relationship(
        back_populates="novel",
        cascade="all, delete-orphan",
    )


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("novel_id", "name", name="uq_character_novel_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    novel_id: Mapped[str] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    voice_id: Mapped[str] = mapped_column(String(80), default="")
    voice_name: Mapped[str] = mapped_column(String(120), default="")
    voice_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    gender: Mapped[str] = mapped_column(String(20), default="")
    age: Mapped[str] = mapped_column(String(20), default="")
    personality: Mapped[str] = mapped_column(String(20), default="")
    is_narrator: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    novel: Mapped[Novel] = relationship(back_populates="characters")


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    novel_id: Mapped[str] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    source_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="imported")
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    subtitle_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    novel: Mapped[Novel] = relationship(back_populates="episodes")
    lines: Mapped[list["ScriptLine"]] = relationship(
        back_populates="episode",
        cascade="all, delete-orphan",
        order_by="ScriptLine.position",
    )
    scenes: Mapped[list["Scene"]] = relationship(
        back_populates="episode",
        cascade="all, delete-orphan",
        order_by="Scene.position",
    )
    jobs: Mapped[list["Job"]] = relationship(
        back_populates="episode",
        cascade="all, delete-orphan",
    )


class ScriptLine(Base):
    __tablename__ = "script_lines"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20), default="dialogue")
    text: Mapped[str] = mapped_column(Text)
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_sec: Mapped[float | None] = mapped_column(Float, nullable=True)

    episode: Mapped[Episode] = relationship(back_populates="lines")


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    episode: Mapped[Episode] = relationship(back_populates="scenes")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    novel_id: Mapped[str | None] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), nullable=True, index=True)
    episode_id: Mapped[str | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    line_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(200), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    novel: Mapped[Novel | None] = relationship(back_populates="jobs")
    episode: Mapped[Episode | None] = relationship(back_populates="jobs")
