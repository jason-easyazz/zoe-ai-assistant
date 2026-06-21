"""Tests for ui_orchestrator allowed action types."""

import sys
import os
import json

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui_orchestrator import ALLOWED_ACTION_TYPES, enqueue_ui_action


def test_panel_navigate_allowed():
    assert "panel_navigate" in ALLOWED_ACTION_TYPES


def test_panel_clear_allowed():
    assert "panel_clear" in ALLOWED_ACTION_TYPES


def test_panel_show_fullscreen_allowed():
    assert "panel_show_fullscreen" in ALLOWED_ACTION_TYPES


def test_panel_announce_allowed():
    assert "panel_announce" in ALLOWED_ACTION_TYPES


def test_panel_request_auth_allowed():
    assert "panel_request_auth" in ALLOWED_ACTION_TYPES


def test_panel_set_mode_allowed():
    assert "panel_set_mode" in ALLOWED_ACTION_TYPES


def test_existing_types_not_broken():
    for t in ("navigate", "open_panel", "focus", "fill", "notify", "refresh", "click"):
        assert t in ALLOWED_ACTION_TYPES, f"Broken: {t} missing from ALLOWED_ACTION_TYPES"


def test_panel_show_action_form_allowed():
    assert "panel_show_action_form" in ALLOWED_ACTION_TYPES


def test_panel_update_field_allowed():
    assert "panel_update_field" in ALLOWED_ACTION_TYPES


def test_panel_list_update_allowed():
    assert "panel_list_update" in ALLOWED_ACTION_TYPES


def test_panel_close_action_form_allowed():
    assert "panel_close_action_form" in ALLOWED_ACTION_TYPES


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _DedupDb:
    def __init__(self):
        self.queries = []

    async def execute(self, sql, params=()):
        self.queries.append((sql, params))
        if 'FROM ui_panel_sessions' in sql:
            return _Cursor({'user_id': 'panel-user'})
        if 'FROM ui_actions' in sql:
            return _Cursor({
                'id': 'existing-action',
                'status': 'queued',
                'action_type': 'show_card',
                'payload': json.dumps({'type': 'skybridge', 'card': {'content': {'title': 'Weather'}}}),
                'panel_id': 'panel-1',
                'chat_session_id': None,
                'requires_confirmation': 0,
                'confirmation_token': None,
            })
        raise AssertionError(f'unexpected SQL: {sql}')


@pytest.mark.asyncio
async def test_enqueue_ui_action_dedup_returns_broadcast_message_shape():
    result = await enqueue_ui_action(
        _DedupDb(),
        user_id='voice-user',
        panel_id='panel-1',
        action_type='show_card',
        payload={'type': 'skybridge'},
        idempotency_key='same-turn',
        broadcast=False,
    )

    assert result['id'] == 'existing-action'
    assert result['action_id'] == 'existing-action'
    assert result['action_type'] == 'show_card'
    assert result['panel_id'] == 'panel-1'
    assert result['payload']['type'] == 'skybridge'
    assert result['requires_confirmation'] is False
    assert result['deduped'] is True
