"""
Transactions Management System
Handles financial transactions (income/expense) with linking to calendar events and list items
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
import sqlite3
import json
import os
from auth_integration import validate_session, AuthenticatedSession

router = APIRouter(prefix="/api/transactions", tags=["transactions"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def get_connection(row_factory=None):
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    if row_factory is not None:
        conn.row_factory = row_factory
    try:
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass
    return conn

def init_transactions_db():
    """Initialize transactions table"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
            transaction_date DATE NOT NULL,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'completed')),
            payment_method TEXT DEFAULT 'bank',
            person_id INTEGER,
            calendar_event_id INTEGER,
            list_item_id INTEGER,
            metadata JSON DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id),
            FOREIGN KEY (calendar_event_id) REFERENCES events(id),
            FOREIGN KEY (list_item_id) REFERENCES list_items(id)
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_user_date 
        ON transactions(user_id, transaction_date)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_person 
        ON transactions(person_id)
    """)
    
    conn.commit()
    conn.close()

# Initialize on import
init_transactions_db()

# Request/Response models
class TransactionCreate(BaseModel):
    description: str
    amount: float
    type: str  # "income" or "expense"
    transaction_date: str  # YYYY-MM-DD
    payment_method: Optional[str] = "bank"
    person_id: Optional[int] = None
    calendar_event_id: Optional[int] = None
    list_item_id: Optional[int] = None
    metadata: Optional[Dict] = {}

class TransactionUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[float] = None
    type: Optional[str] = None
    transaction_date: Optional[str] = None
    status: Optional[str] = None
    payment_method: Optional[str] = None
    person_id: Optional[int] = None
    metadata: Optional[Dict] = None

class TransactionResponse(BaseModel):
    id: int
    user_id: str
    description: str
    amount: float
    type: str
    transaction_date: str
    status: str
    payment_method: str
    person_id: Optional[int]
    calendar_event_id: Optional[int]
    list_item_id: Optional[int]
    metadata: Optional[Dict]
    created_at: str
    updated_at: str

@router.post("")
async def create_transaction(transaction: TransactionCreate, session: AuthenticatedSession = Depends(validate_session)):
    """Create a new transaction"""
    user_id = session.user_id
    conn = get_connection()
    cursor = conn.cursor()
    
    # Validate type
    if transaction.type not in ['income', 'expense']:
        conn.close()
        raise HTTPException(status_code=400, detail="Type must be 'income' or 'expense'")
    
    # Validate status if provided
    if transaction.status and transaction.status not in ['pending', 'completed']:
        conn.close()
        raise HTTPException(status_code=400, detail="Status must be 'pending' or 'completed'")
    
    cursor.execute("""
        INSERT INTO transactions (user_id, description, amount, type, transaction_date, 
                                  payment_method, person_id, calendar_event_id, list_item_id, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, transaction.description, transaction.amount, transaction.type,
        transaction.transaction_date, transaction.payment_method, transaction.person_id,
        transaction.calendar_event_id, transaction.list_item_id,
        json.dumps(transaction.metadata) if transaction.metadata else '{}'
    ))
    
    transaction_id = cursor.lastrowid
    
    # Update linked calendar event or list item if they exist
    if transaction.calendar_event_id:
        try:
            cursor.execute("""
                UPDATE events SET transaction_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
            """, (transaction_id, transaction.calendar_event_id, user_id))
        except Exception:
            pass  # Column might not exist yet
    
    if transaction.list_item_id:
        try:
            cursor.execute("""
                UPDATE list_items SET transaction_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND EXISTS (
                    SELECT 1 FROM lists WHERE lists.id = list_items.list_id AND lists.user_id = ?
                )
            """, (transaction_id, transaction.list_item_id, user_id))
        except Exception:
            pass  # Column might not exist yet
    
    conn.commit()
    conn.close()
    
    return {
        "id": transaction_id,
        "message": "Transaction created successfully",
        "transaction": {
            "id": transaction_id,
            "description": transaction.description,
            "amount": transaction.amount,
            "type": transaction.type,
            "transaction_date": transaction.transaction_date,
            "status": "pending"
        }
    }

@router.get("")
async def get_transactions(
    start_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    type: Optional[str] = Query(None, description="Type filter (income/expense)"),
    status: Optional[str] = Query(None, description="Status filter (pending/completed)"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get transactions with optional filtering"""
    user_id = session.user_id
    conn = get_connection(row_factory=sqlite3.Row)
    cursor = conn.cursor()
    
    query = "SELECT * FROM transactions WHERE user_id = ?"
    params = [user_id]
    
    if start_date:
        query += " AND transaction_date >= ?"
        params.append(start_date)
    
    if end_date:
        query += " AND transaction_date <= ?"
        params.append(end_date)
    
    if type:
        query += " AND type = ?"
        params.append(type)
    
    if status:
        query += " AND status = ?"
        params.append(status)
    
    query += " ORDER BY transaction_date DESC, created_at DESC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    transactions = []
    for row in rows:
        try:
            metadata = json.loads(row['metadata']) if row['metadata'] else {}
        except Exception:
            metadata = {}
        
        transactions.append({
            "id": row['id'],
            "description": row['description'],
            "amount": row['amount'],
            "type": row['type'],
            "transaction_date": row['transaction_date'],
            "status": row['status'],
            "payment_method": row['payment_method'],
            "person_id": row['person_id'],
            "calendar_event_id": row['calendar_event_id'],
            "list_item_id": row['list_item_id'],
            "metadata": metadata,
            "created_at": row['created_at'],
            "updated_at": row['updated_at']
        })
    
    conn.close()
    return {"transactions": transactions, "count": len(transactions)}

@router.get("/{transaction_id}")
async def get_transaction(transaction_id: int, session: AuthenticatedSession = Depends(validate_session)):
    """Get a specific transaction"""
    user_id = session.user_id
    conn = get_connection(row_factory=sqlite3.Row)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    try:
        metadata = json.loads(row['metadata']) if row['metadata'] else {}
    except Exception:
        metadata = {}
    
    return {
        "id": row['id'],
        "description": row['description'],
        "amount": row['amount'],
        "type": row['type'],
        "transaction_date": row['transaction_date'],
        "status": row['status'],
        "payment_method": row['payment_method'],
        "person_id": row['person_id'],
        "calendar_event_id": row['calendar_event_id'],
        "list_item_id": row['list_item_id'],
        "metadata": metadata,
        "created_at": row['created_at'],
        "updated_at": row['updated_at']
    }

@router.put("/{transaction_id}")
async def update_transaction(transaction_id: int, transaction: TransactionUpdate, session: AuthenticatedSession = Depends(validate_session)):
    """Update a transaction"""
    user_id = session.user_id
    conn = get_connection()
    cursor = conn.cursor()
    
    # Check if transaction exists
    cursor.execute("SELECT * FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    # Build update query
    update_fields = []
    params = []
    
    update_data = transaction.dict(exclude_unset=True)
    
    for field, value in update_data.items():
        if field == "metadata" and value is not None:
            update_fields.append(f"{field} = ?")
            params.append(json.dumps(value))
        elif field == "type" and value not in ['income', 'expense']:
            conn.close()
            raise HTTPException(status_code=400, detail="Type must be 'income' or 'expense'")
        elif field == "status" and value not in ['pending', 'completed']:
            conn.close()
            raise HTTPException(status_code=400, detail="Status must be 'pending' or 'completed'")
        else:
            update_fields.append(f"{field} = ?")
            params.append(value)
    
    if update_fields:
        update_fields.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([transaction_id, user_id])
        
        query = f"UPDATE transactions SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?"
        cursor.execute(query, params)
        conn.commit()
    
    conn.close()
    
    return {"message": "Transaction updated successfully", "id": transaction_id}

@router.delete("/{transaction_id}")
async def delete_transaction(transaction_id: int, session: AuthenticatedSession = Depends(validate_session)):
    """Delete a transaction"""
    user_id = session.user_id
    conn = get_connection()
    cursor = conn.cursor()
    
    # Check if transaction exists
    cursor.execute("SELECT id FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    cursor.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id))
    conn.commit()
    conn.close()
    
    return {"message": "Transaction deleted successfully"}

@router.patch("/{transaction_id}/status")
async def update_transaction_status(transaction_id: int, session: AuthenticatedSession = Depends(validate_session)):
    """Toggle transaction status between pending and completed"""
    user_id = session.user_id
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get current status
    cursor.execute("SELECT status FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    current_status = row[0]
    new_status = 'completed' if current_status == 'pending' else 'pending'
    
    cursor.execute("""
        UPDATE transactions 
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
    """, (new_status, transaction_id, user_id))
    
    conn.commit()
    conn.close()
    
    return {
        "message": "Transaction status updated successfully",
        "id": transaction_id,
        "status": new_status
    }

@router.put("/{transaction_id}/move")
async def move_transaction(transaction_id: int, new_date: str = Query(..., description="New date (YYYY-MM-DD)"), session: AuthenticatedSession = Depends(validate_session)):
    """Move a transaction to a different date"""
    user_id = session.user_id
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verify transaction exists and belongs to user
    cursor.execute("SELECT id FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    cursor.execute("""
        UPDATE transactions 
        SET transaction_date = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
    """, (new_date, transaction_id, user_id))
    
    conn.commit()
    conn.close()
    
    return {
        "message": "Transaction moved successfully",
        "id": transaction_id,
        "transaction_date": new_date
    }

@router.get("/summary/week")
async def get_week_summary(start_date: str = Query(..., description="Start date of week (YYYY-MM-DD)"), session: AuthenticatedSession = Depends(validate_session)):
    """Get financial summary for a week"""
    user_id = session.user_id
    conn = get_connection(row_factory=sqlite3.Row)
    cursor = conn.cursor()
    
    # Calculate week range
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = start + timedelta(days=6)
    
    cursor.execute("""
        SELECT 
            type,
            status,
            SUM(amount) as total
        FROM transactions
        WHERE user_id = ? AND transaction_date >= ? AND transaction_date <= ?
        GROUP BY type, status
    """, (user_id, start.isoformat(), end.isoformat()))
    
    rows = cursor.fetchall()
    
    summary = {
        "income_expected": 0,
        "income_received": 0,
        "expense_due": 0,
        "expense_paid": 0
    }
    
    for row in rows:
        total = row['total']
        if row['type'] == 'income':
            if row['status'] == 'pending':
                summary['income_expected'] = total
            elif row['status'] == 'completed':
                summary['income_received'] = total
        elif row['type'] == 'expense':
            if row['status'] == 'pending':
                summary['expense_due'] = total
            elif row['status'] == 'completed':
                summary['expense_paid'] = total
    
    conn.close()
    return summary

