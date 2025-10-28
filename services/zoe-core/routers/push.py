"""
Push Notification API Endpoints
Handles subscription management and notification sending
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

from models.push_subscription import (
    PushSubscriptionRequest,
    PushSubscriptionResponse,
    NotificationPreferences,
    NotificationPayload,
    PushTestRequest
)
from services.push_notification_service import get_push_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/push", tags=["push-notifications"])


@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Get the VAPID public key for client-side subscription"""
    push_service = get_push_service()
    return {
        "publicKey": push_service.get_public_key()
    }


@router.post("/subscribe", response_model=PushSubscriptionResponse)
async def subscribe_to_push(
    subscription: PushSubscriptionRequest,
    session: AuthenticatedSession = Depends(validate_session)
    """Subscribe user to push notifications"""
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        push_service = get_push_service()
        subscription_id = await push_service.save_subscription(
            user_id=user_id,
            endpoint=subscription.endpoint,
            p256dh=subscription.keys.p256dh,
            auth=subscription.keys.auth,
            user_agent=subscription.user_agent,
            device_type=subscription.device_type
        )
        
        logger.info(f"✅ User {user_id} subscribed to push notifications")
        
        return PushSubscriptionResponse(
            success=True,
            subscription_id=subscription_id,
            message="Successfully subscribed to push notifications"
        )
    
    except Exception as e:
        logger.error(f"❌ Error subscribing to push: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/unsubscribe", response_model=PushSubscriptionResponse)
async def unsubscribe_from_push(
    subscription: PushSubscriptionRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Unsubscribe from push notifications"""
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        push_service = get_push_service()
        await push_service.remove_subscription(subscription.endpoint)
        
        logger.info(f"✅ User {user_id} unsubscribed from push notifications")
        
        return PushSubscriptionResponse(
            success=True,
            message="Successfully unsubscribed from push notifications"
        )
    
    except Exception as e:
        logger.error(f"❌ Error unsubscribing from push: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/preferences")
async def get_notification_preferences(
    session: AuthenticatedSession = Depends(validate_session)
    """Get user's notification preferences"""
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        push_service = get_push_service()
        preferences = await push_service.get_user_preferences(user_id)
        return preferences
    
    except Exception as e:
        logger.error(f"❌ Error getting preferences: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/preferences")
async def update_notification_preferences(
    preferences: NotificationPreferences,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update user's notification preferences"""
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        push_service = get_push_service()
        await push_service.update_preferences(
            user_id, preferences.model_dump()
        )
        
        logger.info(f"✅ Updated preferences for user {user_id}")
        
        return {
            "success": True,
            "message": "Preferences updated successfully"
        }
    
    except Exception as e:
        logger.error(f"❌ Error updating preferences: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send")
async def send_notification(
    payload: NotificationPayload,
    target_user_id: str,
    session: AuthenticatedSession = Depends(validate_session)
    """Send a notification to a specific user (admin/system use)"""
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # TODO: Add permission check - only admins or system can send to other users
    
    try:
        push_service = get_push_service()
        result = await push_service.send_notification(target_user_id, payload)
        
        if result["success"]:
            logger.info(f"✅ Sent notification to user {target_user_id}: {result['sent']} delivered")
        else:
            logger.warning(f"⚠️ Failed to send notification to user {target_user_id}: {result.get('message')}")
        
        return result
    
    except Exception as e:
        logger.error(f"❌ Error sending notification: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test")
async def send_test_notification(
    test_request: PushTestRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Send a test notification to the current user"""
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        push_service = get_push_service()
        
        payload = NotificationPayload(
            title=test_request.title,
            body=test_request.body,
            url=test_request.url,
            icon="/icons/icon-192.png",
            badge="/icons/icon-72.png",
            tag="test-notification"
        )
        
        result = await push_service.send_notification(user_id, payload)
        
        return {
            "success": result["success"],
            "message": f"Test notification sent to {result['sent']} device(s)",
            "details": result
        }
    
    except Exception as e:
        logger.error(f"❌ Error sending test notification: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/subscriptions")
async def get_user_subscriptions(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get all active subscriptions for the current user"""
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        push_service = get_push_service()
        subscriptions = await push_service.get_user_subscriptions(user_id)
        
        # Don't return sensitive keys, just metadata
        safe_subs = [
            {
                "id": sub["id"],
                "device_type": sub["device_type"],
                "endpoint_domain": sub["endpoint"].split("/")[2] if "/" in sub["endpoint"] else "unknown"
            }
            for sub in subscriptions
        ]
        
        return {
            "subscriptions": safe_subs,
            "count": len(safe_subs)
        }
    
    except Exception as e:
        logger.error(f"❌ Error getting subscriptions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

