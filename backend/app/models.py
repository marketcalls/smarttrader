from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BrokerConfig(Base):
    """Single row (id=1) holding the TradeSmart app credentials."""

    __tablename__ = "broker_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key: Mapped[str | None] = mapped_column(Text)
    api_secret_enc: Mapped[str | None] = mapped_column(Text)
    redirect_url: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuthSession(Base):
    __tablename__ = "auth_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # SHA-256 of the cookie value; the raw cookie value is never stored.
    session_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    user_id: Mapped[str | None] = mapped_column(Text)
    user_name: Mapped[str | None] = mapped_column(Text)
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SymToken(Base):
    __tablename__ = "symtoken"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    brsymbol: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String)
    exchange: Mapped[str | None] = mapped_column(String)
    brexchange: Mapped[str | None] = mapped_column(String)
    token: Mapped[str | None] = mapped_column(String)
    expiry: Mapped[str | None] = mapped_column(String)
    strike: Mapped[float | None] = mapped_column(Float)
    lotsize: Mapped[int | None] = mapped_column(Integer)
    instrumenttype: Mapped[str | None] = mapped_column(String)
    tick_size: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        Index("idx_symtoken_symbol_exchange", "symbol", "exchange"),
        Index("idx_symtoken_token", "token"),
        Index("idx_symtoken_brsymbol", "brsymbol"),
    )


class MasterContractStatus(Base):
    """Single row (id=1) tracking the latest master contract download."""

    __tablename__ = "master_contract_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String, default="idle", nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    total_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exchanges_json: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class AgentSetting(Base):
    """Single row (id=1): which model the agent uses and any extra instructions."""

    __tablename__ = "agent_setting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String, nullable=False)
    reasoning_effort: Mapped[str | None] = mapped_column(String)
    instructions: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class AgentSecret(Base):
    """Encrypted agent credentials, for example the ChatGPT OAuth tokens."""

    __tablename__ = "agent_secret"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    value_enc: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class AgentConversation(Base):
    __tablename__ = "agent_conversation"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AgentMessage(Base):
    """The transcript shown in the UI. The model's own context lives in Agno's session store."""

    __tablename__ = "agent_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tools_json: Mapped[str | None] = mapped_column(Text)
    usage_json: Mapped[str | None] = mapped_column(Text)
    # A run paused for order approval: {"run_id", "requirements", "state"}.
    pending_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AgentRisk(Base):
    """Single row (id=1): whether the agent may trade, and the limits checked on every order."""

    __tablename__ = "agent_risk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_order_value: Mapped[float] = mapped_column(Float, default=50_000.0, nullable=False)
    max_quantity: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    max_orders_per_day: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    allowed_exchanges: Mapped[str] = mapped_column(Text, default="NSE,BSE", nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class AgentOrderAudit(Base):
    """Every order the agent proposed, the user's decision and the broker's answer."""

    __tablename__ = "agent_order_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String, index=True)
    run_id: Mapped[str | None] = mapped_column(String)
    tool: Mapped[str] = mapped_column(String, nullable=False)
    args_json: Mapped[str] = mapped_column(Text, nullable=False)
    # proposed | approved | rejected | refused (risk check) | placed | failed
    status: Mapped[str] = mapped_column(String, nullable=False)
    order_id: Mapped[str | None] = mapped_column(String)
    message: Mapped[str | None] = mapped_column(Text)
