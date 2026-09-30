from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, JSON, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    msg_id = Column(String(64), unique=True, index=True)
    msg_type = Column(Integer)
    content = Column(Text)
    sender_wxid = Column(String(64), index=True)
    sender_name = Column(String(128))
    room_id = Column(String(64), index=True, default="")
    room_name = Column(String(128))
    is_group = Column(Boolean, default=False)
    create_time = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)


class AutoReplyRule(Base):
    __tablename__ = "auto_reply_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128))
    type = Column(String(32))  # keyword / regex / intent
    patterns = Column(JSON)  # list of patterns
    reply = Column(Text, default="")
    workflow = Column(String(128), default="")
    priority = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class MessageTemplate(Base):
    __tablename__ = "message_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True)
    type = Column(String(32))  # card / form / text / list
    title = Column(String(256), default="")
    content = Column(Text)
    footer = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Workflow(Base):
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True)
    description = Column(String(512))
    trigger_intents = Column(JSON)
    states = Column(JSON)  # state machine definition
    forward_to = Column(String(128), default="")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ForwardRule(Base):
    __tablename__ = "forward_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128))
    trigger = Column(String(256))  # workflow:xxx.FORWARD / keyword:a,b,c
    targets = Column(JSON)  # list of chatroom ids
    template = Column(String(256))
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class ChatStatistic(Base):
    __tablename__ = "chat_statistics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String(64), index=True)
    user_wxid = Column(String(64), index=True)
    user_name = Column(String(128))
    message_count = Column(Integer, default=0)
    stat_date = Column(String(10))  # YYYY-MM-DD
    stat_type = Column(String(16))  # daily / weekly / monthly
    created_at = Column(DateTime, default=datetime.now)


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(32), unique=True, index=True)
    user_wxid = Column(String(64))
    user_name = Column(String(128))
    game = Column(String(64))
    rank = Column(String(64))
    hours = Column(Float)
    budget = Column(Float)
    notes = Column(Text, default="")
    status = Column(String(32), default="pending")  # pending/confirmed/assigned/done/cancelled
    assignee_wxid = Column(String(64), default="")
    assignee_name = Column(String(128), default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(128), unique=True, index=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# Database engine factory
def create_db_engine(db_url: str):
    return create_engine(db_url, echo=False)


def create_session(engine):
    return sessionmaker(bind=engine)
