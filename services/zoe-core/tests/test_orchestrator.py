import pytest
import sys
import os
from unittest.mock import AsyncMock, patch

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from life_orchestrator import LifeOrchestrator

@pytest.mark.asyncio
async def test_analyze_everything_success():
    orchestrator = LifeOrchestrator()

    # Mock all _api_get calls to return successful data
    mock_api_get = AsyncMock()
    mock_api_get.side_effect = [
        [{"name": "John Doe"}],  # people
        {"tasks": [{"name": "Task 1"}]},  # tasks
        [{"start": {"dateTime": "2025-10-07T09:00:00"}}], # calendar
        [{"name": "Collection 1"}], # collections
        [{"title": "Reminder 1"}], # reminders
        {"results": [{"content": "Memory 1"}]} # memory
    ]

    with patch('life_orchestrator.LifeOrchestrator._api_get', mock_api_get):
        result = await orchestrator.analyze_everything("test_user", {})

        assert "error" not in result["people_insights"]
        assert result["people_insights"]["total"] == 1

        assert "error" not in result["task_insights"]
        assert result["task_insights"]["total"] == 1

        assert "error" not in result["calendar_insights"]
        assert result["calendar_insights"]["upcoming_events_count"] == 1
        
        assert "error" not in result["collections_insights"]
        assert result["collections_insights"]["total"] == 1

        assert "error" not in result["reminders_insights"]
        assert result["reminders_insights"]["total"] == 1
        
        assert "error" not in result["memory_insights"]
        assert result["memory_insights"]["total_memories"] == 1

@pytest.mark.asyncio
async def test_analyze_everything_api_failures():
    orchestrator = LifeOrchestrator()

    # Mock all _api_get calls to return None (failure)
    with patch('life_orchestrator.LifeOrchestrator._api_get', AsyncMock(return_value=None)):
        result = await orchestrator.analyze_everything("test_user", {})

        assert "error" in result["people_insights"]
        assert "error" in result["task_insights"]
        assert "error" in result["calendar_insights"]
        assert "error" in result["collections_insights"]
        assert "error" in result["reminders_insights"]
        assert "error" in result["memory_insights"]

        # Ensure suggestions are empty when data fetching fails
        assert len(result["urgent_actions"]) == 0
        assert len(result["smart_suggestions"]) == 0
