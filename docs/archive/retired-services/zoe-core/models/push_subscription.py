"""Pydantic models for push notification endpoints."""

from pydantic import BaseModel
from typing import Optional


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionRequest(BaseModel):
    endpoint: str
    keys: PushKeys
    user_agent: Optional[str] = None
    device_type: Optional[str] = "unknown"


class PushSubscriptionResponse(BaseModel):
    success: bool
    subscription_id: Optional[int] = None
    message: str


class NotificationPreferences(BaseModel):
    enabled: bool = True
    chat_notifications: bool = True
    reminder_notifications: bool = True
    system_notifications: bool = True
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None


class NotificationPayload(BaseModel):
    title: str
    body: str
    url: Optional[str] = None
    icon: Optional[str] = "/icons/icon-192.png"
    badge: Optional[str] = "/icons/icon-72.png"
    tag: Optional[str] = None
    data: Optional[dict] = None


class PushTestRequest(BaseModel):
    title: str = "Test Notification"
    body: str = "This is a test notification from Zoe"
    url: Optional[str] = "/"
