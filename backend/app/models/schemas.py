from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- Auth ---
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- Messages ---
class MessageOut(BaseModel):
    msg_id: str
    msg_type: int
    content: str
    sender_wxid: str
    sender_name: str = ""
    room_id: str = ""
    room_name: str = ""
    is_group: bool = False
    create_time: datetime = Field(default_factory=datetime.now)


class MessageListResponse(BaseModel):
    items: list[MessageOut]
    total: int
    page: int
    size: int


class SendMessageRequest(BaseModel):
    msg: str
    receiver: str
    aters: str = ""


# --- Auto Reply Rules ---
class RuleCreate(BaseModel):
    name: str
    type: str = "keyword"
    patterns: list[str]
    reply: str = ""
    workflow: str = ""
    priority: int = 0
    enabled: bool = True


class RuleOut(RuleCreate):
    id: int
    created_at: datetime
    updated_at: datetime


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    patterns: Optional[list[str]] = None
    reply: Optional[str] = None
    workflow: Optional[str] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None


# --- Templates ---
class TemplateCreate(BaseModel):
    name: str
    type: str = "text"
    title: str = ""
    content: str
    footer: str = ""


class TemplateOut(TemplateCreate):
    id: int
    created_at: datetime
    updated_at: datetime


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    footer: Optional[str] = None


# --- Workflows ---
class WorkflowState(BaseModel):
    name: str
    on_enter: str
    transitions: list[dict] = []


class WorkflowCreate(BaseModel):
    name: str
    description: str = ""
    trigger_intents: list[str] = []
    states: list[WorkflowState] = []
    forward_to: str = ""
    enabled: bool = True


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    trigger_intents: Optional[list[str]] = None
    states: Optional[list[WorkflowState]] = None
    forward_to: Optional[str] = None
    enabled: Optional[bool] = None


class WorkflowOut(WorkflowCreate):
    id: int
    created_at: datetime
    updated_at: datetime


# --- Forward Rules ---
class ForwardRuleCreate(BaseModel):
    name: str
    trigger: str
    targets: list[str]
    template: str = ""
    enabled: bool = True


class ForwardRuleOut(ForwardRuleCreate):
    id: int
    created_at: datetime


# --- Statistics ---
class RankingItem(BaseModel):
    user_wxid: str
    user_name: str
    message_count: int


class TimelineItem(BaseModel):
    hour: int
    count: int


class KeywordItem(BaseModel):
    word: str
    count: int
    score: float


class StatisticsOverview(BaseModel):
    total_messages: int
    active_users: int
    active_rooms: int
    ranking: list[RankingItem] = []
    timeline: list[TimelineItem] = []
    keywords: list[KeywordItem] = []


# --- Orders ---
class OrderOut(BaseModel):
    order_id: str
    user_wxid: str
    user_name: str
    game: str
    rank: str
    hours: float
    budget: float
    notes: str = ""
    status: str
    assignee_name: str = ""
    created_at: datetime


# --- System Config ---
class SystemConfigItem(BaseModel):
    key: str
    value: str


class SystemConfigUpdate(BaseModel):
    items: list[SystemConfigItem]


# --- Dashboard ---
class DashboardOverview(BaseModel):
    platform: str
    wechat_online: bool
    today_messages: int
    active_rooms: int
    ai_calls: int
    pending_orders: int
