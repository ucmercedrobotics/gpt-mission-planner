import os
from typing import Optional, Tuple

from lxml import etree

from app.xml_types import AttributeTags, ControlTags, ActionTags

# Compiling an XSD is not cheap, and multi-robot planning validates every
# candidate schema on every retry. Keyed by (path, mtime) so edits to the
# schemas submodule are picked up without a restart.
_SCHEMA_CACHE: dict[tuple[str, float], etree.XMLSchema] = {}


def parse_schema_location(xml_mp: str) -> str:
    root: etree._Element = etree.fromstring(xml_mp)
    location = root.attrib[AttributeTags.SchemaLocation]
    return location


def _load_schema(schema_path: str) -> etree.XMLSchema:
    key = (schema_path, os.path.getmtime(schema_path))
    schema = _SCHEMA_CACHE.get(key)
    if schema is None:
        with open(schema_path, "rb") as schema_file:
            schema_root = etree.XML(schema_file.read())
        schema = etree.XMLSchema(schema_root)
        _SCHEMA_CACHE[key] = schema
    return schema


def parse_code(mp_out: str | None, code_type: str = "xml") -> str:
    assert isinstance(mp_out, str)

    xml_response: str = mp_out.split("```" + code_type + "\n")[1]
    xml_response = xml_response.split("```")[0]

    return xml_response


def validate_output(schema_path: str, xml_mp: str) -> Tuple[bool, str]:
    try:
        schema = _load_schema(schema_path)

        # Parse the XML file
        root: etree._Element = etree.fromstring(xml_mp)

        # Validate the XML file against the XSD schema
        schema.assertValid(root)
        return True, "XML is valid."

    except etree.XMLSchemaError as e:
        return False, "XML is invalid: " + str(e)
    except Exception as e:
        return False, "An error occurred: " + str(e)


def validate_any(
    schema_paths: list[str], xml_mp: str
) -> Tuple[bool, Optional[str], str]:
    """Validate against whichever of `schema_paths` accepts the document.

    The schema the model declared in `schema_location` is tried first as a fast
    path, then every remaining candidate. Returns (ok, matched_schema, message).

    Trying them all matters because `schema_location` is model-authored text: a
    plan can name one schema and actually conform to another. On total failure
    the error reported is the one from the declared schema, since that is the
    feedback most likely to help the model fix its own output.
    """
    if not schema_paths:
        return False, None, "No schemas available to validate against."

    declared: Optional[str] = None
    try:
        declared = parse_schema_location(xml_mp)
    except (KeyError, etree.XMLSyntaxError, ValueError):
        # Missing or unparseable schema_location is itself a validation
        # failure, but let the real validator produce the message.
        declared = None

    ordered = list(dict.fromkeys(([declared] if declared in schema_paths else []) + schema_paths))

    declared_error: Optional[str] = None
    first_error: Optional[str] = None
    for candidate in ordered:
        ok, message = validate_output(candidate, xml_mp)
        if ok:
            return True, candidate, message
        if candidate == declared:
            declared_error = message
        if first_error is None:
            first_error = message

    error = declared_error or first_error or "XML did not validate against any known schema."
    if declared and declared not in schema_paths:
        error = (
            f"Declared schema_location '{declared}' is not one of the available schemas "
            f"({', '.join(schema_paths)}). {error}"
        )
    return False, None, error


def count_xml_tasks(xml_mp: str):
    # Parse the XML file
    root: etree._Element = etree.fromstring(xml_mp)
    task_count: int = 0

    # we're parsing before validation, so be careful
    bt: etree._Element = root.find(ControlTags.BehaviorTree)

    fallback: etree._Element = (
        bt.findall(".//" + ControlTags.Fallback) if bt is not None else None
    )

    # count Conditionals only under Fallbacks
    for fb in fallback:
        task_count += len(fb.findall(ControlTags.Sequence))

    # count Actions
    for a in ActionTags:
        task_count += len(root.findall(".//" + a))

    return task_count
