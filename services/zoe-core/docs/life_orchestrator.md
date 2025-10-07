# Life Orchestrator, HITL, and Shared State Documentation

This document outlines the architecture and implementation of the new proactive AI features in Zoe, including the Life Orchestrator, Human-in-the-Loop (HITL), and Shared State systems.

## 1. Architecture Overview

### Life Orchestrator (`life_orchestrator.py`)

The Life Orchestrator is a centralized intelligence system that analyzes data from all of Zoe's subsystems to generate proactive insights and suggestions. It is designed to be a read-only system to ensure it does not cause breaking changes.

-   **Key Responsibilities**:
    -   Concurrently fetch data from various internal APIs (people, tasks, calendar, etc.).
    -   Analyze the combined data to identify urgent actions, opportunities, and smart suggestions.
    -   Provide a single endpoint for the UI to get these insights.

### Shared State Manager (`shared_state.py`)

A thread-safe, in-memory singleton for sharing state across different components of the `zoe-core` service. This can be used for caching, session information, or inter-component communication without direct coupling.

### Human-in-the-Loop (HITL) Manager (`hitl.py`)

A skeleton system for managing actions that require user approval. It provides a basic framework for creating, approving, and rejecting requests. This is not yet integrated into any workflows but is available for future use with destructive operations.

## 2. New API Endpoints

### GET `/api/orchestrator/insights`

-   **Description**: Retrieves a comprehensive analysis of the user's life, including urgent actions, opportunities, and smart suggestions.
-   **Method**: `GET`
-   **Query Parameters**:
    -   `user_id` (str, required): The ID of the user.
-   **Responses**:
    -   `200 OK`: Returns a JSON object with the analysis.
    -   `500 Internal Server Error`: If an error occurs during analysis.

## 3. Feature Flags

The new UI features are controlled by client-side feature flags, allowing for easy enabling/disabling without backend changes.

-   **`chat.html`**:
    -   **Flag**: `ENABLE_PROACTIVE_SUGGESTIONS`
    -   **Location**: Inside the `DOMContentLoaded` event listener script block.
    -   **Effect**: When `true`, the proactive suggestions bar is shown above the chat.

-   **`touch/dashboard.html`**:
    -   **Flag**: `ENABLE_ORB_SUGGESTIONS`
    -   **Location**: Inside the `startZoeConversation()` function.
    -   **Effect**: When `true`, clicking the orb opens the insights panel instead of starting voice recognition directly.

## 4. Rollback Plan

To disable the new features and roll back to the previous behavior:

1.  **Set the feature flags to `false`**:
    -   In `services/zoe-ui/dist/chat.html`, change `const ENABLE_PROACTIVE_SUGGESTIONS = true;` to `false`.
    -   In `services/zoe-ui/dist/touch/dashboard.html`, change `const ENABLE_ORB_SUGGESTIONS = true;` to `false`.
2.  These changes will immediately disable the new UI components, reverting to the previous chat and orb functionality. No backend changes are required for rollback.

## 5. Testing

### Backend Tests

Unit tests for the `LifeOrchestrator` are located in `services/zoe-core/tests/test_orchestrator.py`.

-   **To run the tests**:
    ```bash
    python3 -m pytest /home/pi/services/zoe-core/tests/test_orchestrator.py
    ```

### Frontend Smoke Tests

Simple, automated smoke tests are built into the UI and can be run via a URL query parameter.

-   **`chat.html`**:
    -   **URL**: `https://zoe.local/chat.html?test=true`
    -   **Action**: Opens the browser's developer console and checks if the test logs "SMOKE TEST PASSED!".

-   **`touch/dashboard.html`**:
    -   **URL**: `https://zoe.local/touch/dashboard.html?test=true`
    -   **Action**: Opens the browser's developer console and checks if the test logs "ORB SMOKE TEST PASSED!".


