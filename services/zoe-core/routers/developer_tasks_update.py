# Add this import at the top of developer_tasks.py
from .task_executor import TaskExecutor

# Replace the execute_task_async function with this:
async def execute_task_async(task_id: str, plan: Dict[str, Any]):
    """Execute task with the new task executor"""
    executor = TaskExecutor()
    
    try:
        # Execute the task with full tracking
        result = await executor.execute_task(task_id, plan)
        
        logger.info(f"Task {task_id} execution completed: {result['status']}")
        
        # Send notification if needed (webhook, email, etc.)
        if result["status"] == "completed":
            logger.info(f"✅ Task {task_id} completed successfully")
        elif result["status"] == "failed":
            logger.error(f"❌ Task {task_id} failed: {result.get('errors', [])}")
        
    except Exception as e:
        logger.error(f"Task execution failed: {str(e)}")
        
        # Update task status to failed
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE dynamic_tasks 
            SET status = 'failed', last_executed_at = ?
            WHERE id = ?
        """, (datetime.now(), task_id))
        conn.commit()
        conn.close()
