from __future__ import annotations
"""All Part 2 domain models. Kept lightweight — Pydantic BaseModel with optional fields
so routers can accept partial updates. Storage still uses raw dicts + hub_utils.serialize."""
from datetime import datetime
from typing import Literal, Optional, List
from pydantic import BaseModel, EmailStr, Field
from hub_utils import utc_iso


# ------------------------- Marketplace -------------------------

class CityIn(BaseModel):
    name: str
    state: str = "Bihar"
    status: Literal["active", "paused", "planned"] = "active"


class VendorIn(BaseModel):
    name: str
    contact_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    city: str
    kyc_status: Literal["pending", "approved", "rejected"] = "pending"
    active: bool = True
    rating: float = 4.5
    notes: Optional[str] = None


class VehicleIn(BaseModel):
    model: str
    kind: Literal["bike", "scooter", "ebike"] = "scooter"
    plate: str
    vendor_id: Optional[str] = None
    city: str
    hourly_rate: float = 40.0
    daily_rate: float = 399.0
    status: Literal["available", "booked", "maintenance", "retired"] = "available"


class CustomerIn(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    city: str
    kyc_status: Literal["pending", "approved", "rejected"] = "pending"


class BookingIn(BaseModel):
    customer_id: str
    vehicle_id: str
    city: str
    start_time: str
    end_time: str
    amount: float
    status: Literal["pending", "confirmed", "active", "completed", "cancelled"] = "pending"


class PricingIn(BaseModel):
    name: str
    city: str
    hourly: float = 40.0
    daily: float = 399.0
    weekly: float = 1999.0
    monthly: float = 6499.0
    active: bool = True


class CouponIn(BaseModel):
    code: str
    discount_pct: float = 10.0
    valid_from: Optional[str] = None
    valid_till: Optional[str] = None
    usage_limit: int = 100
    used_count: int = 0
    active: bool = True


class KycIn(BaseModel):
    subject_type: Literal["vendor", "customer"]
    subject_id: str
    subject_name: str
    doc_type: Literal["aadhaar", "pan", "dl", "gst", "cin", "other"] = "aadhaar"
    status: Literal["pending", "approved", "rejected"] = "pending"
    notes: Optional[str] = None


class SupportIn(BaseModel):
    subject: str
    description: str
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    status: Literal["open", "in_progress", "resolved", "closed"] = "open"


class ReviewIn(BaseModel):
    booking_id: Optional[str] = None
    customer_name: str
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = None
    rating: float = 5.0
    comment: Optional[str] = None


# ------------------------- Tasks -------------------------

TaskStatus = Literal["todo", "in_progress", "review", "completed", "cancelled"]
TaskPriority = Literal["low", "medium", "high", "urgent"]


class SubtaskIn(BaseModel):
    title: str
    done: bool = False


class TaskCommentIn(BaseModel):
    body: str
    attachments: List[str] = Field(default_factory=list)
    attachment_name: Optional[str] = None


class TaskIn(BaseModel):
    title: str
    description: Optional[str] = ""
    status: TaskStatus = "todo"
    priority: TaskPriority = "medium"
    assignee_id: Optional[str] = None
    reporter_id: Optional[str] = None
    module: str = "General"
    due_date: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    subtasks: List[SubtaskIn] = Field(default_factory=list)
    attachments: List[str] = Field(default_factory=list)
    link: Optional[str] = None


class TaskStatusPatch(BaseModel):
    status: TaskStatus


# ------------------------- Employees -------------------------

class DepartmentIn(BaseModel):
    name: str
    head_id: Optional[str] = None
    description: Optional[str] = None


class EmployeeInviteIn(BaseModel):
    email: str
    name: str
    role: str = "Employee"
    designation: Optional[str] = None
    department: Optional[str] = None
    phone: Optional[str] = None


class AttendanceIn(BaseModel):
    employee_id: str
    date: str  # YYYY-MM-DD
    status: Literal["present", "absent", "leave", "half_day", "wfh"] = "present"
    check_in: Optional[str] = None
    check_out: Optional[str] = None


class LeaveIn(BaseModel):
    employee_id: str
    from_date: str
    to_date: str
    kind: Literal["casual", "sick", "earned", "unpaid"] = "casual"
    reason: str
    status: Literal["pending", "approved", "rejected"] = "pending"


class PerformanceIn(BaseModel):
    employee_id: str
    period: str  # e.g. Q1-2026
    score: float = 4.0  # 1-5
    highlights: Optional[str] = None
    growth_areas: Optional[str] = None


# ------------------------- Opportunities -------------------------

OpportunityType = Literal[
    "Grant", "Investor", "Accelerator", "Incubator", "Competition",
    "Government Scheme", "Tender", "CSR", "Partnership", "Workshop", "Conference",
]

OpportunityStatus = Literal["open", "assigned", "in_progress", "won", "lost", "closed"]


class OpportunityIn(BaseModel):
    title: str
    type: OpportunityType
    description: Optional[str] = ""
    organisation: Optional[str] = None
    deadline: Optional[str] = None
    value_lakhs: Optional[float] = None
    status: OpportunityStatus = "open"
    assignee_id: Optional[str] = None
    documents: List[str] = Field(default_factory=list)
    link: Optional[str] = None


class OpportunityAssign(BaseModel):
    assignee_id: str


class OpportunityStatusPatch(BaseModel):
    status: OpportunityStatus


# ------------------------- WavyGo Connect -------------------------

ChannelKind = Literal["channel", "dm", "group", "announcement"]


class ChannelIn(BaseModel):
    name: str
    kind: ChannelKind = "channel"
    description: Optional[str] = None
    members: List[str] = Field(default_factory=list)  # list of user_ids


class MessageIn(BaseModel):
    body: str
    attachments: List[str] = Field(default_factory=list)
