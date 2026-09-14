from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.gto_club import assert_gto_club_id
from api.schemas import CommandCreate, CommandUpdate, CommandRead
from db.connection import get_db_dependency
from db.models import Club, CustomCommand

router = APIRouter(prefix="/api", tags=["commands"], dependencies=[Depends(get_current_admin)])


@router.get("/clubs/{club_id}/commands", response_model=List[CommandRead])
def list_commands(
    club_id: int,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    assert_gto_club_id(role, club_id, db)
    return [CommandRead.model_validate(c) for c in club.custom_commands]


@router.post("/clubs/{club_id}/commands", response_model=CommandRead, status_code=201)
def create_command(
    club_id: int,
    body: CommandCreate,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    assert_gto_club_id(role, club_id, db)
    data = body.model_dump()
    data["command_name"] = data["command_name"].lower()
    cmd = CustomCommand(club_id=club_id, **data)
    db.add(cmd)
    db.flush()
    db.refresh(cmd)
    return CommandRead.model_validate(cmd)


@router.put("/commands/{cmd_id}", response_model=CommandRead)
def update_command(
    cmd_id: int,
    body: CommandUpdate,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    cmd = db.query(CustomCommand).get(cmd_id)
    if not cmd:
        raise HTTPException(404, "Command not found")
    assert_gto_club_id(role, int(cmd.club_id), db)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "command_name" and value is not None:
            value = value.lower()
        setattr(cmd, field, value)
    db.flush()
    db.refresh(cmd)
    return CommandRead.model_validate(cmd)


@router.delete("/commands/{cmd_id}", status_code=204)
def delete_command(
    cmd_id: int,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    cmd = db.query(CustomCommand).get(cmd_id)
    if not cmd:
        raise HTTPException(404, "Command not found")
    assert_gto_club_id(role, int(cmd.club_id), db)
    db.delete(cmd)
