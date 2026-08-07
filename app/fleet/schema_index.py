"""Locating and fingerprinting the XSDs in the `schemas` submodule.

Robots identify their action pool by schema stem ("amiga_btcpp"). The planner
has to turn that into a path it can put in the prompt and validate against, and
it is worth knowing when the robot's copy of the XSD differs from ours.
"""

import hashlib
from pathlib import Path
from typing import Optional

# app/fleet/schema_index.py -> app/ -> repo root -> schemas/
SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "schemas"


def schema_stem(value: str) -> str:
    """Normalise 'schemas/amiga_btcpp.xsd', 'amiga_btcpp.xsd' or the bare stem."""
    value = value.strip()
    if value.endswith(".xsd"):
        value = value[: -len(".xsd")]
    return value.rsplit("/", 1)[-1]


def schema_path(stem: str, schemas_dir: Path = SCHEMAS_DIR) -> str:
    """Path to put in `schema_paths` / `schema_location`.

    Returned relative to the repo root when possible, because that is what the
    existing pipeline embeds in prompts and what `validate_output` opens.
    """
    absolute = (schemas_dir / f"{schema_stem(stem)}.xsd").resolve()
    try:
        return str(absolute.relative_to(Path.cwd()))
    except ValueError:
        return str(absolute)


def schema_exists(stem: str, schemas_dir: Path = SCHEMAS_DIR) -> bool:
    return (schemas_dir / f"{schema_stem(stem)}.xsd").is_file()


def list_schema_stems(schemas_dir: Path = SCHEMAS_DIR) -> list[str]:
    if not schemas_dir.exists():
        return []
    return sorted(p.stem for p in schemas_dir.glob("*.xsd") if p.is_file())


def hash_schema(stem: str, schemas_dir: Path = SCHEMAS_DIR) -> Optional[str]:
    path = schemas_dir / f"{schema_stem(stem)}.xsd"
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def schema_hashes(schemas_dir: Path = SCHEMAS_DIR) -> dict[str, Optional[str]]:
    """stem -> sha256, for every XSD the planner has."""
    return {stem: hash_schema(stem, schemas_dir) for stem in list_schema_stems(schemas_dir)}
