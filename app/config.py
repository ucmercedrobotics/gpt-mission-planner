import yaml
from dataclasses import dataclass

@dataclass
class Point:
    lon: float
    lat: float

@dataclass
class Dimension:
    row: int
    col: int

@dataclass
class Polygon:
    points: list[Point]
    dimensions: list[Dimension]

@dataclass
class Config:
    logging: str
    token: str
    ltl: bool
    max_retries: int
    max_tokens: int
    temperature: int
    log_directory: str
    schema: list[str]
    context_files: list[str]
    farm_polygon: Polygon
    host: str
    port: int
    ltl: bool
    promela_template: str
    spin_path: str
