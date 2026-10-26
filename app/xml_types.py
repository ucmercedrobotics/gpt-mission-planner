from enum import Enum


class AttributeTags(str, Enum):
    SchemaLocation = "schema_location"


# TODO: probably could parse this straight from XSD or action msgs
class ActionTags(str, Enum):
    DetectObject = "DetectObject"
    MoveToGPSLocation = "MoveToGPSLocation"
    TakeAmbientTemperature = "TakeAmbientTemperature"
    TakeCO2Reading = "TakeCO2Reading"
    TakeThermalPicture = "TakeThermalPicture"
    SampleLeaf = "SampleLeaf"
    MoveToRelativeLocation = "MoveToRelativeLocation"
    MoveToTreeID = "MoveToTreeID"
    MoveToAisleHead = "MoveToAisleHead"
    OrientRobotHeading = "OrientRobotHeading"
    ActivateSeeder = "ActivateSeeder"
    DeactivateSeeder = "DeactivateSeeder"
    SelectSeed = "SelectSeed"


class ConditionalTags(str, Enum):
    AssertTrue = "AssertTrue"
    CheckValue = "CheckValue"


class ControlTags(str, Enum):
    BehaviorTree = "BehaviorTree"
    Fallback = "Fallback"
    Sequence = "Sequence"
    Parallel = "Parallel"
    Inverter = "Inverter"
    ReactiveSequence = "ReactiveSequence"
    RetryUntilSuccessful = "RetryUntilSuccessful"
