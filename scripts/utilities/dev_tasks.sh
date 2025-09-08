#!/bin/bash
# Developer Task Management Helper
# For managing developer tasks after installation

API_BASE="http://localhost:8000/api/developer/tasks"

case "$1" in
    create)
        if [ -z "$2" ]; then
            echo "Usage: $0 create \"title\" [description]"
            exit 1
        fi
        curl -s -X POST $API_BASE/create \
            -H "Content-Type: application/json" \
            -d "{
                \"title\": \"$2\",
                \"description\": \"${3:-No description}\",
                \"task_type\": \"${4:-development}\",
                \"priority\": \"${5:-medium}\"
            }" | jq '.'
        ;;
    
    list)
        echo "📋 Developer Tasks:"
        curl -s $API_BASE/list | jq '.'
        ;;
    
    status)
        if [ -z "$2" ]; then
            echo "Usage: $0 status <task_id>"
            exit 1
        fi
        curl -s $API_BASE/status/$2 | jq '.'
        ;;
    
    analyze)
        if [ -z "$2" ]; then
            echo "Usage: $0 analyze <task_id>"
            exit 1
        fi
        curl -s -X POST $API_BASE/analyze/$2 | jq '.'
        ;;
    
    execute)
        if [ -z "$2" ]; then
            echo "Usage: $0 execute <task_id>"
            exit 1
        fi
        curl -s -X POST $API_BASE/execute/$2 | jq '.'
        ;;
    
    info)
        curl -s $API_BASE/ | jq '.'
        ;;
    
    *)
        echo "Developer Task Management"
        echo "Usage: $0 {create|list|status|analyze|execute|info}"
        echo ""
        echo "Examples:"
        echo "  $0 create \"Fix API bug\" \"The calendar API returns 500\""
        echo "  $0 list"
        echo "  $0 analyze <task_id>"
        echo "  $0 execute <task_id>"
        ;;
esac
