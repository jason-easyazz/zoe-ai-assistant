from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class EventCreate(BaseModel):
    title: str
    start_date: str
    start_time: Optional[str] = None
    end_date: Optional[str] = None
    end_time: Optional[str] = None
    duration: Optional[int] = None
    category: str = "general"
    location: Optional[str] = None
    all_day: bool = False
    recurring: Optional[str] = None
    metadata: Optional[dict] = None
    visibility: str = "family"


class EventUpdate(BaseModel):
    title: Optional[str] = None
    start_date: Optional[str] = None
    start_time: Optional[str] = None
    end_date: Optional[str] = None
    end_time: Optional[str] = None
    duration: Optional[int] = None
    category: Optional[str] = None
    location: Optional[str] = None
    all_day: Optional[bool] = None
    recurring: Optional[str] = None
    metadata: Optional[dict] = None
    visibility: Optional[str] = None


class ListCreate(BaseModel):
    name: str
    list_type: str = "shopping"
    description: Optional[str] = None
    visibility: str = "family"


class ListUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[str] = None


class ListItemCreate(BaseModel):
    text: str
    priority: str = "normal"
    category: Optional[str] = None
    quantity: Optional[str] = None
    parent_id: Optional[str] = None
    assigned_to: Optional[str] = None


class ListItemUpdate(BaseModel):
    text: Optional[str] = None
    completed: Optional[bool] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[str] = None
    sort_order: Optional[int] = None
    assigned_to: Optional[str] = None


class PersonCreate(BaseModel):
    name: str
    relationship: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    birthday: Optional[str] = None
    notes: Optional[str] = None
    preferences: Optional[dict] = None
    custom_fields: Optional[dict] = None
    visibility: str = "family"


class PersonUpdate(BaseModel):
    name: Optional[str] = None
    relationship: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    birthday: Optional[str] = None
    notes: Optional[str] = None
    preferences: Optional[dict] = None
    custom_fields: Optional[dict] = None
    visibility: Optional[str] = None


class PeopleFieldDefinitionCreate(BaseModel):
    field_key: str
    label: str
    field_type: str = "text"
    required: bool = False
    options: Optional[List[str]] = None
    scope: str = "person"
    sort_order: int = 100
    visibility: str = "family"
    is_active: bool = True


class PeopleFieldDefinitionUpdate(BaseModel):
    label: Optional[str] = None
    field_type: Optional[str] = None
    required: Optional[bool] = None
    options: Optional[List[str]] = None
    scope: Optional[str] = None
    sort_order: Optional[int] = None
    visibility: Optional[str] = None
    is_active: Optional[bool] = None


class MemoryProposalCreate(BaseModel):
    content: str
    title: Optional[str] = None
    memory_type: str = "fact"
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_type: str = "manual"
    source_id: Optional[str] = None
    source_excerpt: Optional[str] = None
    visibility: str = "personal"
    provenance: Optional[dict] = None


class MemoryReviewBody(BaseModel):
    action: str
    note: Optional[str] = None
    content: Optional[str] = None


class ReminderCreate(BaseModel):
    title: str
    description: Optional[str] = None
    reminder_type: str = "one-time"
    category: str = "general"
    priority: str = "normal"
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    recurring_pattern: Optional[str] = None
    visibility: str = "personal"


class ReminderUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    is_active: Optional[bool] = None
    visibility: Optional[str] = None


class NoteCreate(BaseModel):
    title: Optional[str] = None
    content: str
    category: str = "general"
    tags: Optional[List[str]] = None
    visibility: str = "personal"


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    visibility: Optional[str] = None


class JournalEntryCreate(BaseModel):
    title: Optional[str] = None
    content: str
    mood: Optional[str] = None
    mood_score: Optional[int] = None
    tags: Optional[List[str]] = None
    weather: Optional[str] = None
    location: Optional[str] = None
    photos: Optional[List[str]] = None
    privacy_level: str = "personal"


class JournalEntryUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    mood: Optional[str] = None
    mood_score: Optional[int] = None
    tags: Optional[List[str]] = None
    weather: Optional[str] = None
    location: Optional[str] = None


class TransactionCreate(BaseModel):
    description: str
    amount: float
    type: str = "expense"
    transaction_date: str
    payment_method: Optional[str] = None
    status: str = "completed"
    person_id: Optional[str] = None
    calendar_event_id: Optional[str] = None
    metadata: Optional[dict] = None
    visibility: str = "family"


class TransactionUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[float] = None
    type: Optional[str] = None
    transaction_date: Optional[str] = None
    payment_method: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[dict] = None


class WeatherPreferences(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    city: Optional[str] = None
    country: Optional[str] = None
    temperature_unit: str = "celsius"
    use_current_location: bool = False


class SnoozeBody(BaseModel):
    snooze_minutes: int = 15
