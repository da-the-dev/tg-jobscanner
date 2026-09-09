"""SQLAlchemy models: message cache, verdict cache, application tracking."""
from sqlalchemy import (BigInteger, ForeignKey, Index, Integer, String, Text,
                        func, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Pipeline order matters: it drives the dashboard's filter pills.
STATUSES = ("new", "applied", "interviewing", "offer", "rejected", "skipped")

NOW = text("(datetime('now'))")


class Base(DeclarativeBase):
    pass


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)
    last_msg_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    messages: Mapped[list["Message"]] = relationship(back_populates="channel")


class Message(Base):
    __tablename__ = "messages"

    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("channels.id"),
                                            primary_key=True)
    msg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    date: Mapped[str | None] = mapped_column(String)
    text: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str | None] = mapped_column(String)
    link: Mapped[str | None] = mapped_column(String)
    # new | filtered_out | duplicate | scored | error
    status: Mapped[str] = mapped_column(String, default="new", server_default="new")

    channel: Mapped[Channel] = relationship(back_populates="messages")

    __table_args__ = (
        Index("idx_messages_hash", "text_hash"),
        Index("idx_messages_status", "status"),
    )


class Verdict(Base):
    """One LLM judgement per unique posting text (cross-posts share a hash)."""
    __tablename__ = "verdicts"

    text_hash: Mapped[str] = mapped_column(String, primary_key=True)
    relevant: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String)
    company: Mapped[str | None] = mapped_column(String)
    salary: Mapped[str | None] = mapped_column(String)
    location: Mapped[str | None] = mapped_column(String)
    reasons_apply: Mapped[str | None] = mapped_column(Text)   # JSON arrays
    reasons_skip: Mapped[str | None] = mapped_column(Text)
    strengths: Mapped[str | None] = mapped_column(Text)
    weaknesses: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str | None] = mapped_column(String, server_default=NOW)


class Application(Base):
    """Where you stand with a posting. Keyed by text_hash like verdicts, so the
    status survives re-renders and follows cross-posted duplicates."""
    __tablename__ = "applications"

    text_hash: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, default="new", server_default="new")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    applied_at: Mapped[str | None] = mapped_column(String)  # first time it hit 'applied'
    updated_at: Mapped[str | None] = mapped_column(String, server_default=NOW,
                                                   onupdate=func.datetime("now"))

    __table_args__ = (Index("idx_applications_status", "status"),)


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text_hash: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    at: Mapped[str | None] = mapped_column(String, server_default=NOW)

    __table_args__ = (Index("idx_events_hash", "text_hash", "at"),)
