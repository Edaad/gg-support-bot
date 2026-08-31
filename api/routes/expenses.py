"""Admin-only CRUD and XLSX export for expenses."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy.orm import Session, joinedload

from api.auth import require_admin
from api.schemas import ExpenseCreate, ExpenseRead, ExpenseUpdate
from db.connection import get_db_dependency
from db.models import Club, Expense

router = APIRouter(
    prefix="/api/expenses",
    tags=["expenses"],
    dependencies=[Depends(require_admin)],
)


def _to_read(row: Expense) -> ExpenseRead:
    return ExpenseRead(
        id=row.id,
        amount=row.amount,
        expense_type=row.expense_type,
        description=row.description,
        club_id=row.club_id,
        club_name=row.club.name if row.club else None,
        expense_date=row.expense_date,
        pending=bool(row.pending),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_amount(amount: Decimal) -> None:
    if amount == 0:
        raise HTTPException(400, "Amount must not be zero")


def _require_club(db: Session, club_id: int) -> Club:
    club = db.get(Club, club_id)
    if not club:
        raise HTTPException(400, "Club not found")
    return club


def _filtered_query(
    db: Session,
    *,
    club_id: Optional[int],
    pending: Optional[bool],
    q: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
):
    query = db.query(Expense).options(joinedload(Expense.club))
    if club_id is not None:
        query = query.filter(Expense.club_id == club_id)
    if pending is not None:
        query = query.filter(Expense.pending.is_(pending))
    if date_from is not None:
        query = query.filter(Expense.expense_date >= date_from)
    if date_to is not None:
        query = query.filter(Expense.expense_date <= date_to)
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        query = query.join(Club, Expense.club_id == Club.id).filter(
            (Expense.expense_type.ilike(needle))
            | (Expense.description.ilike(needle))
            | (Club.name.ilike(needle))
        )
    return query.order_by(Expense.expense_date.desc(), Expense.id.desc())


@router.get("", response_model=List[ExpenseRead])
def list_expenses(
    club_id: Optional[int] = Query(None),
    pending: Optional[bool] = Query(None),
    q: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    db: Session = Depends(get_db_dependency),
):
    rows = _filtered_query(
        db,
        club_id=club_id,
        pending=pending,
        q=q,
        date_from=date_from,
        date_to=date_to,
    ).all()
    return [_to_read(r) for r in rows]


@router.post("", response_model=ExpenseRead, status_code=201)
def create_expense(body: ExpenseCreate, db: Session = Depends(get_db_dependency)):
    _validate_amount(body.amount)
    expense_type = (body.expense_type or "").strip()
    if not expense_type:
        raise HTTPException(400, "Expense type is required")
    _require_club(db, body.club_id)
    row = Expense(
        amount=body.amount,
        expense_type=expense_type,
        description=(body.description or "").strip() or None,
        club_id=body.club_id,
        expense_date=body.expense_date,
        pending=body.pending,
    )
    db.add(row)
    db.flush()
    row = (
        db.query(Expense)
        .options(joinedload(Expense.club))
        .filter(Expense.id == row.id)
        .one()
    )
    return _to_read(row)


@router.get("/export")
def export_expenses(
    club_id: Optional[int] = Query(None),
    pending: Optional[bool] = Query(None),
    q: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    db: Session = Depends(get_db_dependency),
):
    rows = _filtered_query(
        db,
        club_id=club_id,
        pending=pending,
        q=q,
        date_from=date_from,
        date_to=date_to,
    ).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Expenses"
    ws.append(
        [
            "Amount",
            "Expense type",
            "Description",
            "Club",
            "Date",
            "Pending",
            "Created at",
        ]
    )
    for r in rows:
        ws.append(
            [
                float(r.amount) if r.amount is not None else None,
                r.expense_type,
                r.description or "",
                r.club.name if r.club else "",
                r.expense_date.isoformat() if r.expense_date else "",
                "yes" if r.pending else "no",
                r.created_at.isoformat() if r.created_at else "",
            ]
        )

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    from_label = date_from.isoformat() if date_from else "all"
    to_label = date_to.isoformat() if date_to else "all"
    filename = f"expenses-{from_label}-to-{to_label}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/{expense_id}", response_model=ExpenseRead)
def update_expense(
    expense_id: int,
    body: ExpenseUpdate,
    db: Session = Depends(get_db_dependency),
):
    row = db.query(Expense).options(joinedload(Expense.club)).filter(Expense.id == expense_id).first()
    if not row:
        raise HTTPException(404, "Expense not found")
    data = body.model_dump(exclude_unset=True)
    if "amount" in data:
        _validate_amount(data["amount"])
    if "expense_type" in data:
        et = (data["expense_type"] or "").strip()
        if not et:
            raise HTTPException(400, "Expense type is required")
        data["expense_type"] = et
    if "description" in data and data["description"] is not None:
        data["description"] = data["description"].strip() or None
    if "club_id" in data:
        _require_club(db, data["club_id"])
    for field, value in data.items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    db.refresh(row)
    return _to_read(row)


@router.delete("/{expense_id}", status_code=204)
def delete_expense(expense_id: int, db: Session = Depends(get_db_dependency)):
    row = db.get(Expense, expense_id)
    if not row:
        raise HTTPException(404, "Expense not found")
    db.delete(row)
