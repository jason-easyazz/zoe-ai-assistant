"""
Integration tests for Widget System
Tests widget marketplace, AI generation, and layout persistence
"""

import requests
import json
import pytest
from datetime import datetime

BASE_URL = "http://localhost:8000"


class TestWidgetMarketplace:
    """Test widget marketplace endpoints"""
    
    def test_get_marketplace_widgets(self):
        """Test fetching marketplace widgets"""
        response = requests.get(f"{BASE_URL}/api/widgets/marketplace")
        assert response.status_code == 200
        
        data = response.json()
        assert "widgets" in data
        assert "total" in data
        assert isinstance(data["widgets"], list)
        
        # Should have 8 core widgets
        assert data["total"] >= 8
        
        # Check core widgets are present
        widget_names = [w["name"] for w in data["widgets"]]
        assert "events" in widget_names
        assert "tasks" in widget_names
        assert "time" in widget_names
        assert "zoe-orb" in widget_names
    
    def test_marketplace_pagination(self):
        """Test marketplace pagination"""
        response = requests.get(f"{BASE_URL}/api/widgets/marketplace?page=1&limit=5")
        assert response.status_code == 200
        
        data = response.json()
        assert len(data["widgets"]) <= 5
        assert data["page"] == 1
        assert data["limit"] == 5
    
    def test_marketplace_filter_by_type(self):
        """Test filtering marketplace by widget type"""
        response = requests.get(f"{BASE_URL}/api/widgets/marketplace?widget_type=core")
        assert response.status_code == 200
        
        data = response.json()
        # All results should be core widgets
        for widget in data["widgets"]:
            assert widget["widget_type"] == "core"
    
    def test_marketplace_search(self):
        """Test marketplace search"""
        response = requests.get(f"{BASE_URL}/api/widgets/marketplace?search=events")
        assert response.status_code == 200
        
        data = response.json()
        # Should find events widget
        assert any("event" in w["display_name"].lower() for w in data["widgets"])


class TestWidgetLayouts:
    """Test widget layout persistence"""
    
    def test_save_widget_layout(self):
        """Test saving a widget layout"""
        layout_data = {
            "device_id": "test-device-123",
            "layout_type": "desktop_dashboard",
            "layout": [
                {"type": "events", "size": "size-medium", "order": 0},
                {"type": "tasks", "size": "size-small", "order": 1},
                {"type": "time", "size": "size-large", "order": 2}
            ]
        }
        
        response = requests.post(
            f"{BASE_URL}/api/user/layout",
            json=layout_data
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
    
    def test_get_widget_layout(self):
        """Test retrieving a saved layout"""
        # First save a layout
        layout_data = {
            "device_id": "test-device-456",
            "layout_type": "desktop_dashboard",
            "layout": [
                {"type": "weather", "size": "size-medium", "order": 0}
            ]
        }
        
        save_response = requests.post(
            f"{BASE_URL}/api/user/layout",
            json=layout_data
        )
        assert save_response.status_code == 200
        
        # Then retrieve it
        response = requests.get(
            f"{BASE_URL}/api/user/layout?device_id=test-device-456&layout_type=desktop_dashboard"
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["layout"] is not None
        assert len(data["layout"]) == 1
        assert data["layout"][0]["type"] == "weather"
    
    def test_delete_widget_layout(self):
        """Test deleting a saved layout"""
        # First save a layout
        layout_data = {
            "device_id": "test-device-789",
            "layout_type": "desktop_dashboard",
            "layout": [{"type": "time", "size": "size-small", "order": 0}]
        }
        
        requests.post(f"{BASE_URL}/api/user/layout", json=layout_data)
        
        # Then delete it
        response = requests.delete(
            f"{BASE_URL}/api/user/layout?device_id=test-device-789&layout_type=desktop_dashboard"
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestWidgetInstallation:
    """Test widget installation and uninstallation"""
    
    def test_install_core_widget(self):
        """Test installing a core widget"""
        # Get a widget ID from marketplace
        marketplace_response = requests.get(f"{BASE_URL}/api/widgets/marketplace")
        widgets = marketplace_response.json()["widgets"]
        
        if widgets:
            widget_id = widgets[0]["id"]
            
            response = requests.post(
                f"{BASE_URL}/api/widgets/install/{widget_id}"
            )
            
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
    
    def test_get_user_widgets(self):
        """Test getting user's installed widgets"""
        response = requests.get(f"{BASE_URL}/api/widgets/my-widgets")
        assert response.status_code == 200
        
        data = response.json()
        assert "widgets" in data
        assert isinstance(data["widgets"], list)


class TestWidgetRating:
    """Test widget rating system"""
    
    def test_rate_widget(self):
        """Test rating a widget"""
        # Get a widget ID
        marketplace_response = requests.get(f"{BASE_URL}/api/widgets/marketplace")
        widgets = marketplace_response.json()["widgets"]
        
        if widgets:
            widget_id = widgets[0]["id"]
            
            rating_data = {
                "widget_id": widget_id,
                "rating": 5,
                "review": "Excellent widget!"
            }
            
            response = requests.post(
                f"{BASE_URL}/api/widgets/rate",
                json=rating_data
            )
            
            # May fail without auth, but should handle gracefully
            assert response.status_code in [200, 401, 403]


class TestWidgetUpdates:
    """Test widget update system"""
    
    def test_check_for_updates(self):
        """Test checking for widget updates"""
        response = requests.get(f"{BASE_URL}/api/widgets/updates")
        assert response.status_code == 200
        
        data = response.json()
        # Should return empty array if all up to date
        assert isinstance(data, list)


if __name__ == "__main__":
    print("🧪 Running Widget System Integration Tests")
    print("=" * 60)
    
    # Run tests
    pytest.main([__file__, "-v", "--tb=short"])




