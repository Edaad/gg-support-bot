"""CRUD for broadcast groups — named collections of group chats for targeted broadcasts."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from db.connection import get_db_dependency
from db.models import BroadcastGroup, BroadcastGroupMember, Club, Group

router = APIRouter(
    prefix="/api/clubs",
    tags=["broadcast_groups"],
    dependencies=[Depends(get_current_admin)],
)


class BroadcastGroupCreate(BaseModel):
    name: str


class BroadcastGroupUpdate(BaseModel):
    name: Optional[str] = None


class MemberInfo(BaseModel):
    chat_id: int
    group_name: Optional[str] = None


class BroadcastGroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    club_id: int
    name: str
    member_count: int = 0
    members: List[MemberInfo] = []
    created_at: Optional[datetime] = None


def _bg_to_read(bg: BroadcastGroup, session: Session) -> BroadcastGroupRead:
    members = []
    for m in bg.members:
        g = session.query(Group).filter_by(chat_id=m.chat_id).first()
        members.append(MemberInfo(chat_id=m.chat_id, group_name=g.name if g else None))
    return BroadcastGroupRead(
        id=bg.id,
        club_id=bg.club_id,
        name=bg.name,
        member_count=len(bg.members),
        members=members,
        created_at=bg.created_at,
    )


@router.get("/{club_id}/broadcast-groups", response_model=List[BroadcastGroupRead])
def list_broadcast_groups(club_id: int, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    bgs = db.query(BroadcastGroup).filter_by(club_id=club_id).order_by(BroadcastGroup.name).all()
    return [_bg_to_read(bg, db) for bg in bgs]


@router.post("/{club_id}/broadcast-groups", response_model=BroadcastGroupRead, status_code=201)
def create_broadcast_group(club_id: int, body: BroadcastGroupCreate, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    bg = BroadcastGroup(club_id=club_id, name=body.name)
    db.add(bg)
    db.flush()
    db.refresh(bg)
    return _bg_to_read(bg, db)


@router.put("/{club_id}/broadcast-groups/{bg_id}", response_model=BroadcastGroupRead)
def update_broadcast_group(club_id: int, bg_id: int, body: BroadcastGroupUpdate, db: Session = Depends(get_db_dependency)):
    bg = db.query(BroadcastGroup).filter_by(id=bg_id, club_id=club_id).first()
    if not bg:
        raise HTTPException(404, "Broadcast group not found")
    if body.name is not None:
        bg.name = body.name
    db.flush()
    db.refresh(bg)
    return _bg_to_read(bg, db)


@router.delete("/{club_id}/broadcast-groups/{bg_id}", status_code=204)
def delete_broadcast_group(club_id: int, bg_id: int, db: Session = Depends(get_db_dependency)):
    bg = db.query(BroadcastGroup).filter_by(id=bg_id, club_id=club_id).first()
    if not bg:
        raise HTTPException(404, "Broadcast group not found")
    db.delete(bg)


@router.post("/{club_id}/broadcast-groups/{bg_id}/members", response_model=BroadcastGroupRead)
def add_member(club_id: int, bg_id: int, body: MemberInfo, db: Session = Depends(get_db_dependency)):
    bg = db.query(BroadcastGroup).filter_by(id=bg_id, club_id=club_id).first()
    if not bg:
        raise HTTPException(404, "Broadcast group not found")
    existing = db.query(BroadcastGroupMember).filter_by(
        broadcast_group_id=bg_id, chat_id=body.chat_id
    ).first()
    if existing:
        raise HTTPException(409, "Group chat already in this broadcast group")
    db.add(BroadcastGroupMember(broadcast_group_id=bg_id, chat_id=body.chat_id))
    db.flush()
    db.refresh(bg)
    return _bg_to_read(bg, db)


@router.delete("/{club_id}/broadcast-groups/{bg_id}/members/{chat_id}", response_model=BroadcastGroupRead)
def remove_member(club_id: int, bg_id: int, chat_id: int, db: Session = Depends(get_db_dependency)):
    bg = db.query(BroadcastGroup).filter_by(id=bg_id, club_id=club_id).first()
    if not bg:
        raise HTTPException(404, "Broadcast group not found")
    member = db.query(BroadcastGroupMember).filter_by(
        broadcast_group_id=bg_id, chat_id=chat_id
    ).first()
    if not member:
        raise HTTPException(404, "Member not found")
    db.delete(member)
    db.flush()
    db.refresh(bg)
    return _bg_to_read(bg, db)
