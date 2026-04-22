"""
FastAPI router for transactions.
Mounted at prefix="/api/transactions" with tag "transactions".
"""
import json
import uuid
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_db
from guest_policy import require_feature_access
from models import TransactionCreate, TransactionUpdate
from push import broadcaster

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


def _row_to_dict(row) -> dict:
    """Convert aiosqlite Row to dict, parsing metadata JSON."""
    if row is None:
        return None
    d = dict(row)
    if d.get("metadata") is not None and isinstance(d["metadata"], str):
        try:
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else None
        except json.JSONDecodeError:
            d["metadata"] = None
    if "deleted" in d and d["deleted"] is not None:
        d["deleted"] = bool(d["deleted"])
    return d


def _visibility_filter_sql() -> str:
    """SQL fragment: (visibility='family' OR user_id=?) AND deleted=0"""
    return "(visibility = 'family' OR user_id = ?) AND deleted = 0"


@router.post("/", response_model=dict)
async def create_transaction(
    payload: TransactionCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a new transaction."""
    await require_feature_access(db, user, feature="transactions", action="create")
    user_id = user["user_id"]
    transaction_id = str(uuid.uuid4())
    metadata_json = json.dumps(payload.metadata) if payload.metadata else None

    await db.execute(
        """INSERT INTO transactions (
            id, user_id, description, amount, type, transaction_date,
            payment_method, status, person_id, calendar_event_id,
            metadata, visibility, deleted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            transaction_id,
            user_id,
            payload.description,
            payload.amount,
            payload.type,
            payload.transaction_date,
            payload.payment_method,
            payload.status,
            payload.person_id,
            payload.calendar_event_id,
            metadata_json,
            payload.visibility,
        ),
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM transactions WHERE id = ?", [transaction_id])
    row = await cursor.fetchone()
    transaction = _row_to_dict(row)

    await broadcaster.broadcast("transactions", "transaction_created", transaction)
    return transaction


@router.get("/", response_model=dict)
async def list_transactions(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    type: Optional[str] = Query(None, description="Transaction type: expense, income, etc."),
    status: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List transactions with optional filters."""
    await require_feature_access(db, user, feature="transactions", action="read")
    user_id = user["user_id"]
    conditions = [_visibility_filter_sql()]
    params: list = [user_id]

    if start_date:
        conditions.append("transaction_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("transaction_date <= ?")
        params.append(end_date)
    if type:
        conditions.append("type = ?")
        params.append(type)
    if status:
        conditions.append("status = ?")
        params.append(status)

    where = " AND ".join(conditions)
    sql = f"SELECT * FROM transactions WHERE {where} ORDER BY transaction_date DESC, created_at DESC"
    cursor = await db.execute(sql, params)
    rows = await cursor.fetchall()
    transactions = [_row_to_dict(r) for r in rows]
    return {"transactions": transactions}


@router.get("/summary/week", response_model=dict)
async def get_weekly_summary(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get weekly transaction summary."""
    await require_feature_access(db, user, feature="transactions", action="read")
    user_id = user["user_id"]
    end = date.today()
    start = end - timedelta(days=6)
    start_str = start.isoformat()
    end_str = end.isoformat()

    where = f"{_visibility_filter_sql()} AND transaction_date >= ? AND transaction_date <= ?"
    cursor = await db.execute(
        """SELECT type, SUM(amount) as total
         FROM transactions
         WHERE """ + where + """
         GROUP BY type""",
        [user_id, start_str, end_str],
    )
    rows = await cursor.fetchall()
    by_type = {r[0]: r[1] for r in rows}

    cursor = await db.execute(
        """SELECT SUM(amount) FROM transactions
         WHERE """ + where + """ AND type = 'expense'""",
        [user_id, start_str, end_str],
    )
    row = await cursor.fetchone()
    total_expense = row[0] if row and row[0] else 0

    cursor = await db.execute(
        """SELECT SUM(amount) FROM transactions
         WHERE """ + where + """ AND type = 'income'""",
        [user_id, start_str, end_str],
    )
    row = await cursor.fetchone()
    total_income = row[0] if row and row[0] else 0

    return {
        "start_date": start_str,
        "end_date": end_str,
        "by_type": by_type,
        "total_expense": total_expense,
        "total_income": total_income,
        "net": total_income - total_expense,
    }


@router.get("/{transaction_id}", response_model=dict)
async def get_transaction(
    transaction_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get a single transaction by ID."""
    await require_feature_access(db, user, feature="transactions", action="read")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM transactions WHERE " + where,
        [user_id, transaction_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return _row_to_dict(row)


@router.put("/{transaction_id}", response_model=dict)
async def update_transaction(
    transaction_id: str,
    payload: TransactionUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update an existing transaction."""
    await require_feature_access(db, user, feature="transactions", action="update")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM transactions WHERE " + where,
        [user_id, transaction_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    updates = []
    params = []
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key == "metadata":
            updates.append("metadata = ?")
            params.append(json.dumps(value) if value is not None else None)
        else:
            updates.append(f"{key} = ?")
            params.append(value)

    if not updates:
        return _row_to_dict(row)

    updates.append("updated_at = datetime('now')")
    params.append(transaction_id)
    await db.execute(
        f"UPDATE transactions SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM transactions WHERE id = ?", [transaction_id])
    row = await cursor.fetchone()
    transaction = _row_to_dict(row)

    await broadcaster.broadcast("transactions", "transaction_updated", transaction)
    return transaction


@router.delete("/{transaction_id}", response_model=dict)
async def delete_transaction(
    transaction_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft delete a transaction."""
    await require_feature_access(db, user, feature="transactions", action="delete")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM transactions WHERE " + where,
        [user_id, transaction_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    await db.execute(
        "UPDATE transactions SET deleted = 1, updated_at = datetime('now') WHERE id = ?",
        [transaction_id],
    )
    await db.commit()

    await broadcaster.broadcast("transactions", "transaction_deleted", {"id": transaction_id})
    return {"ok": True, "id": transaction_id}


@router.patch("/{transaction_id}/status", response_model=dict)
async def toggle_transaction_status(
    transaction_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Toggle transaction status between pending and completed."""
    await require_feature_access(db, user, feature="transactions", action="patch_status")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM transactions WHERE " + where,
        [user_id, transaction_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    new_status = "completed" if row["status"] == "pending" else "pending"
    await db.execute(
        "UPDATE transactions SET status = ?, updated_at = datetime('now') WHERE id = ?",
        [new_status, transaction_id],
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM transactions WHERE id = ?", [transaction_id])
    row = await cursor.fetchone()
    transaction = _row_to_dict(row)

    await broadcaster.broadcast("transactions", "transaction_updated", transaction)
    return transaction
