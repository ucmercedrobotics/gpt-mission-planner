"""Wire contract between robots and the planner.

Everything a robot sends or receives is defined here. Changing a field in this
file changes what robot integrators must implement, so keep it additive.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Bumped when the TCP framing changes shape. Robots report what they speak so
# the planner can refuse to talk to a receiver it would only confuse.
BT_PROTOCOL_VERSION = "tcp-lenprefix-v1"


class RobotStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"


class Liveness(str, Enum):
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"
    # Restored from the registry file but not heard from since the planner
    # started. Never eligible for work.
    UNKNOWN = "unknown"


class GeoPoint(BaseModel):
    lat: float
    lon: float


class BTEndpoint(BaseModel):
    """Where the robot's behavior tree executor listens."""

    # None means "use the source IP of my registration request". This is the
    # recommended value: a robot rarely knows which of its interfaces the
    # planner can actually reach.
    host: Optional[str] = None
    port: int = Field(ge=1, le=65535)
    protocol: str = BT_PROTOCOL_VERSION


class Capabilities(BaseModel):
    """Optional narrowing of the schema's action pool.

    A robot running amiga_btcpp.xsd may not have every action wired up; listing
    the subset it can actually execute keeps the allocator from assigning work
    it would reject.
    """

    model_config = ConfigDict(extra="allow")

    actions: list[str] = Field(default_factory=list)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    robot_id: str = Field(min_length=1, max_length=128)
    name: Optional[str] = None
    # XSD stem, e.g. "amiga_btcpp" -> schemas/amiga_btcpp.xsd
    schema_name: str = Field(min_length=1, max_length=128, alias="schema")
    schema_sha256: Optional[str] = None
    bt_endpoint: BTEndpoint
    capabilities: Capabilities = Field(default_factory=Capabilities)
    status: RobotStatus = RobotStatus.IDLE
    battery_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    position: Optional[GeoPoint] = None
    heartbeat_interval_s: float = Field(default=5.0, gt=0.0, le=300.0)

    @field_validator("robot_id")
    @classmethod
    def _clean_robot_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("robot_id must not be blank")
        # Used in filenames (logs/missions/<mission>_<robot_id>_result.xml) and
        # in URL paths, so keep it boring.
        if any(c in value for c in "/\\\0"):
            raise ValueError("robot_id must not contain path separators")
        return value

    @field_validator("schema_name")
    @classmethod
    def _clean_schema(cls, value: str) -> str:
        value = value.strip()
        # Accept "amiga_btcpp.xsd" or "schemas/amiga_btcpp.xsd" and reduce to
        # the stem, so integrators can send whichever they have on hand.
        if value.endswith(".xsd"):
            value = value[: -len(".xsd")]
        value = value.rsplit("/", 1)[-1]
        if not value:
            raise ValueError("schema must not be blank")
        return value


class RegisterResponse(BaseModel):
    ok: bool = True
    robot_id: str
    ttl_s: float
    heartbeat_url: str
    # False means the planner has no schemas/<schema>.xsd. The robot stays in
    # the roster so it is visible in the UI, but it cannot be given work.
    schema_known: bool
    schema_matches: Optional[bool] = None


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: RobotStatus = RobotStatus.IDLE
    battery_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    position: Optional[GeoPoint] = None
    current_mission_id: Optional[str] = None


class HeartbeatResponse(BaseModel):
    ok: bool = True


class RobotView(BaseModel):
    """Read model for GET /robots — what the web UI renders."""

    robot_id: str
    name: Optional[str] = None
    schema_name: str = Field(serialization_alias="schema")
    schema_known: bool
    schema_matches: Optional[bool] = None
    host: Optional[str]
    port: int
    protocol: str
    status: RobotStatus
    liveness: Liveness
    eligible: bool
    battery_pct: Optional[float] = None
    position: Optional[GeoPoint] = None
    current_mission_id: Optional[str] = None
    actions: list[str] = Field(default_factory=list)
    last_seen_s_ago: Optional[float] = None
    source: str = "http"


# --- Allocation (LLM output contract) -------------------------------------


class Assignment(BaseModel):
    """One robot's share of a mission, as decided by the allocation LLM."""

    model_config = ConfigDict(extra="ignore")

    robot_id: str
    sub_mission: str = Field(min_length=1)
    assigned_aisles: list[int] = Field(default_factory=list)
    assigned_tree_indices: list[int] = Field(default_factory=list)
    rationale: Optional[str] = None


class AllocationPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    assignments: list[Assignment] = Field(default_factory=list)
    unassigned_reason: Optional[str] = None


class RobotPlan(BaseModel):
    """A generated behavior tree bound to the robot it was generated for.

    `robot_id` -- not `matched_schema` -- is what routing keys on. Two robots
    running the same XSD produce two plans that validate identically, so the
    schema cannot tell them apart; it serves only as a consistency check that
    the model wrote a plan for the platform it was asked about.
    """

    robot_id: str
    xml_path: str
    matched_schema: str
    assignment: Assignment


class FleetMissionResult(BaseModel):
    mission_id: str
    plans: list[RobotPlan] = Field(default_factory=list)
    allocation: AllocationPlan = Field(default_factory=AllocationPlan)
    failures: dict[str, str] = Field(default_factory=dict)


# --- Dispatch -------------------------------------------------------------


class DispatchOutcome(str, Enum):
    DISPATCHED = "dispatched"
    # Robot answered with accepted=false.
    REJECTED = "rejected"
    # Could not connect / send.
    UNREACHABLE = "unreachable"
    # Bytes left the socket but the robot closed without an ack. This is what a
    # robot that has not implemented the ack frame yet looks like.
    UNACKNOWLEDGED = "unacknowledged"


class DispatchResult(BaseModel):
    robot_id: str
    outcome: DispatchOutcome
    host: str
    port: int
    mission_id: Optional[str] = None
    error: Optional[str] = None
    ack: Optional[dict[str, Any]] = None

    @property
    def ok(self) -> bool:
        return self.outcome in (
            DispatchOutcome.DISPATCHED,
            DispatchOutcome.UNACKNOWLEDGED,
        )
