"""Public seating projections: the customer's own place and the anonymous room.

They never carry seating-service identifiers (assignments, visits of other
customers) nor names: only places from the layout, states and group sizes.
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

PlaceId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
PlaceLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
PlaceKind = Literal["table", "bar"]
PlaceState = Literal["free", "held", "occupied"]
Position = Annotated[int, Field(strict=True, ge=1, le=100)]
Capacity = Annotated[int, Field(strict=True, ge=1, le=100)]
PartySize = Annotated[int, Field(strict=True, ge=1, le=20)]
Version = Annotated[int, Field(strict=True, ge=1)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeatingPlace(_Model):
    """A table or the stools of a bar, as the customer sees them."""

    place_id: PlaceId
    kind: PlaceKind
    label: PlaceLabel
    capacity: Capacity
    seats: list[Position] = Field(default_factory=list)

    @model_validator(mode="after")
    def seats_match_kind(self) -> "SeatingPlace":
        if self.kind == "table" and self.seats:
            raise ValueError("A table is assigned whole, without seat positions")
        if self.kind == "bar":
            if not self.seats:
                raise ValueError("Bar places list their stool positions")
            if any(later != earlier + 1 for earlier, later in zip(self.seats, self.seats[1:])):
                raise ValueError("Bar stools of a group are contiguous and ordered")
            if self.seats[-1] > self.capacity:
                raise ValueError("Bar stools must exist in the bar")
        return self


def _fits(place: SeatingPlace, party_size: int) -> None:
    if party_size > place.capacity:
        raise ValueError("The group does not fit in the place")
    if place.kind == "bar" and len(place.seats) != party_size:
        raise ValueError("A bar group takes one stool per person")


class SeatingProposal(_Model):
    """A temporary hold waiting for the customer's explicit decision."""

    proposal_id: PlaceId
    version: Version
    place: SeatingPlace
    party_size: PartySize
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def group_fits(self) -> "SeatingProposal":
        _fits(self.place, self.party_size)
        return self


class SeatingView(_Model):
    """The customer's own seating: nothing, a pending proposal or a confirmed place."""

    status: Literal["none", "proposed", "seated"] = "none"
    proposal: SeatingProposal | None = None
    place: SeatingPlace | None = None
    party_size: PartySize | None = None
    seated_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def fields_match_status(self) -> "SeatingView":
        seated = (self.place, self.party_size, self.seated_at)
        if self.status == "none" and (self.proposal is not None or any(v is not None for v in seated)):
            raise ValueError("No seating carries no proposal or place")
        if self.status == "proposed" and (self.proposal is None or any(v is not None for v in seated)):
            raise ValueError("A proposal is not a place yet")
        if self.status == "seated":
            if self.proposal is not None or any(v is None for v in seated):
                raise ValueError("A seated group has place, size and seated_at")
            assert self.place is not None and self.party_size is not None
            _fits(self.place, self.party_size)
        return self


class RoomSeat(_Model):
    position: Position
    state: PlaceState
    mine: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def free_is_nobodys(self) -> "RoomSeat":
        if self.state == "free" and self.mine:
            raise ValueError("A free stool belongs to nobody")
        return self


class RoomPlace(_Model):
    """One place of the room; other groups are only states and sizes."""

    place_id: PlaceId
    kind: PlaceKind
    label: PlaceLabel
    capacity: Capacity
    display_order: Annotated[int, Field(strict=True, ge=0)]
    state: PlaceState
    party_size: PartySize | None = None
    mine: bool = Field(default=False, strict=True)
    seats: list[RoomSeat] = Field(default_factory=list)

    @model_validator(mode="after")
    def state_is_consistent(self) -> "RoomPlace":
        if self.kind == "table":
            if self.seats:
                raise ValueError("Tables have no stools")
            if (self.state == "free") != (self.party_size is None):
                raise ValueError("Only a taken table has a group size")
            if self.party_size is not None and self.party_size > self.capacity:
                raise ValueError("The group does not fit at the table")
            if self.state == "free" and self.mine:
                raise ValueError("A free table belongs to nobody")
        else:
            if self.party_size is not None:
                raise ValueError("Bar groups are read from their stools")
            if [seat.position for seat in self.seats] != list(range(1, self.capacity + 1)):
                raise ValueError("A bar lists every stool in order")
            if self.mine != any(seat.mine for seat in self.seats):
                raise ValueError("The bar is mine only through my stools")
        return self


class RoomView(_Model):
    """Anonymous room for the plan; ``mine`` marks the caller's own places."""

    schema_version: Literal[1]
    seating_enabled: bool = Field(strict=True)
    generated_at: AwareDatetime
    places: list[RoomPlace] = Field(default_factory=list)

    @model_validator(mode="after")
    def places_are_unique(self) -> "RoomView":
        if not self.seating_enabled and self.places:
            raise ValueError("Without seating there is no room to show")
        ids = [place.place_id for place in self.places]
        if len(ids) != len(set(ids)):
            raise ValueError("Place IDs must be unique")
        return self
