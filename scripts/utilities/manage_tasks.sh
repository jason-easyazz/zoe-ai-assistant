#!/bin/bash
# Task Management Helper
# Location: scripts/utilities/manage_tasks.sh

API_BASE="http://localhost:8000/api"

case "$1" in
    list)
        echo "📋 Listing all tasks..."
        curl -s $API_BASE/tasks/list | jq '.'
        ;;
    
    create)
        if [ -z "$2" ] || [ -z "$3" ]; then
            echo "Usage: $0 create \"title\" \"goal\""
            exit 1
        fi
        echo "🎯 Creating task: $2"
        curl -s -X POST $API_BASE/tasks/create \
            -H "Content-Type: application/json" \
            -d "{\"title\": \"$2\", \"goal\": \"$3\", \"context\": \"${4:-No context}\"}" | jq '.'
        ;;
    
    status)
        if [ -z "$2" ]; then
            echo "Usage: $0 status <task_id>"
            exit 1
        fi
        curl -s $API_BASE/tasks/status/$2 | jq '.'
        ;;
    
    health)
        echo "🏥 Checking task system health..."
        curl -s $API_BASE/tasks/health | jq '.'
        ;;
    
    *)
        echo "Task Management System"
        echo "Usage: $0 {list|create|status|health}"
        ;;
esac
