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


class ConditionalTags(str, Enum):
    AssertTrue = "AssertTrue"
    CheckValue = "CheckValue"


class ControlTags(str, Enum):
    BehaviorTree = "BehaviorTree"
    Fallback = "Fallback"
    Sequence = "Sequence"
    Parallel = "Parallel"
    Inverter = "Inverter"
