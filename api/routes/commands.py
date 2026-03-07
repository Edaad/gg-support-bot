from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas import CommandCreate, CommandUpdate, CommandRead
from db.connection import get_db_dependency
from db.models import Club, CustomCommand

router = APIRouter(prefix="/api", tags=["commands"], dependencies=[Depends(get_current_admin)])


@router.get("/clubs/{club_id}/commands", response_model=List[CommandRead])
def list_commands(club_id: int, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    return [CommandRead.model_validate(c) for c in club.custom_commands]


@router.post("/clubs/{club_id}/commands", response_model=CommandRead, status_code=201)
def create_command(club_id: int, body: CommandCreate, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    cmd = CustomCommand(club_id=club_id, **body.model_dump())
    db.add(cmd)
    db.flush()
    db.refresh(cmd)
    return CommandRead.model_validate(cmd)


@router.put("/commands/{cmd_id}", response_model=CommandRead)
def update_command(cmd_id: int, body: CommandUpdate, db: Session = Depends(get_db_dependency)):
    cmd = db.query(CustomCommand).get(cmd_id)
    if not cmd:
        raise HTTPException(404, "Command not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(cmd, field, value)
    db.flush()
    db.refresh(cmd)
    return CommandRead.model_validate(cmd)


@router.delete("/commands/{cmd_id}", status_code=204)
def delete_command(cmd_id: int, db: Session = Depends(get_db_dependency)):
    cmd = db.query(CustomCommand).get(cmd_id)
    if not cmd:
        raise HTTPException(404, "Command not found")
    db.delete(cmd)
