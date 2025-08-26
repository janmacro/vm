from datetime import datetime
from enum import Enum
from typing import Dict, List, Tuple

from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .db import db


class Event(Enum):
    FR_50 = "50m Free"
    FR_100 = "100m Free"
    FR_200 = "200m Free"
    FR_400 = "400m Free"
    FR_800 = "800m Free"
    FR_1500 = "1500m Free"
    BK_50 = "50m Back"
    BK_100 = "100m Back"
    BK_200 = "200m Back"
    BR_50 = "50m Breast"
    BR_100 = "100m Breast"
    BR_200 = "200m Breast"
    FL_50 = "50m Fly"
    FL_100 = "100m Fly"
    FL_200 = "200m Fly"
    IM_50 = "50m Medley"
    IM_100 = "100m Medley"
    IM_200 = "200m Medley"
    IM_400 = "400m Medley"


class Swimmer(db.Model):
    __tablename__ = "swimmers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(db.String(120), nullable=False)
    gender: Mapped[str] = mapped_column(db.String(1), nullable=False)  # 'm' / 'f'
    active: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=True)

    # One-to-many: delete PBs when swimmer is deleted
    pbs: Mapped[List["PB"]] = relationship(
        back_populates="swimmer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Swimmer id={self.id} name={self.name!r}>"


class PB(db.Model):
    __tablename__ = "pbs"
    __table_args__ = (
        UniqueConstraint("swimmer_id", "event", name="uq_pb_swimmer_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    swimmer_id: Mapped[int] = mapped_column(
        db.ForeignKey("swimmers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    swimmer: Mapped[Swimmer] = relationship(back_populates="pbs")

    # Store the Python Enum name as a string in SQLite (portable)
    event: Mapped[Event] = mapped_column(db.Enum(Event, name="event_enum", native_enum=False), nullable=False)

    points: Mapped[int] = mapped_column(db.Integer, nullable=False)
    time_seconds: Mapped[float | None] = mapped_column(db.Float, nullable=True)

    # Used for swimrankings import
    locked: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=False)
    
    def __repr__(self) -> str:
        return f"<PB swimmer_id={self.swimmer_id} event={self.event.name} points={self.points}>"

    # Helper to expose what optimizer expects
    @staticmethod
    def to_points_dict(swimmers: List[Swimmer]) -> Dict[Tuple[int, Event], int]:
        out: Dict[Tuple[int, Event], int] = {}
        for sw in swimmers:
            for pb in (sw.pbs or []):
                out[(sw.id, pb.event)] = pb.points
        return out
