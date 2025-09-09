#!/bin/bash
# Cursor Task Helper - Easy commands for Cursor IDE

case "$1" in
  "list")
    echo "📋 Current Tasks:"
    curl -s http://localhost:8000/api/developer/tasks/list | jq '.tasks[] | {id, title, status, priority}'
    ;;
  "get")
    echo "📄 Task Details for $2:"
    curl -s http://localhost:8000/api/developer/tasks/status/$2 | jq '.'
    ;;
  "execute")
    echo "🚀 Executing Task $2:"
    curl -s -X POST http://localhost:8000/api/developer/tasks/execute/$2 | jq '.'
    ;;
  "complete")
    echo "✅ Marking Task $2 as completed:"
    curl -s -X PUT http://localhost:8000/api/developer/tasks/$2/status \
      -H "Content-Type: application/json" \
      -d '{"status": "completed"}' | jq '.'
    ;;
  *)
    echo "Usage: $0 {list|get|execute|complete} [task_id]"
    echo "  list     - Show all tasks"
    echo "  get ID   - Get task details"
    echo "  execute ID - Execute a task"
    echo "  complete ID - Mark as completed"
    ;;
esac
