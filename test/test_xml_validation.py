"""Tests for multi-schema validation.

The planner cannot trust `schema_location`: it is text the model wrote. These
cover picking the right XSD out of several, which is what routing a mixed fleet
depends on.
"""

import sys
from pathlib import Path

import pytest

# xml_utils imports `app.xml_types`, and `app/__init__.py` imports its siblings
# by bare name, so both the repo root and app/ have to be importable.
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from app.utils.xml_utils import validate_any, validate_output


ROVER_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" elementFormDefault="qualified">
  <xs:element name="root">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="Drive" minOccurs="1" maxOccurs="unbounded"/>
      </xs:sequence>
      <xs:attribute name="schema_location" type="xs:string" use="required"/>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""

ARM_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" elementFormDefault="qualified">
  <xs:element name="root">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="Grasp" minOccurs="1" maxOccurs="unbounded"/>
      </xs:sequence>
      <xs:attribute name="schema_location" type="xs:string" use="required"/>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""


@pytest.fixture
def schemas(tmp_path):
    rover = tmp_path / "rover.xsd"
    arm = tmp_path / "arm.xsd"
    rover.write_text(ROVER_XSD)
    arm.write_text(ARM_XSD)
    return str(rover), str(arm)


def test_validates_against_declared_schema(schemas):
    rover, arm = schemas
    xml = f'<root schema_location="{rover}"><Drive/></root>'
    ok, matched, _ = validate_any([rover, arm], xml)
    assert ok is True
    assert matched == rover


def test_finds_the_right_schema_when_declaration_is_wrong(schemas):
    """The model named the rover schema but wrote an arm plan.

    Validation still succeeds against the schema the document actually
    conforms to -- which is the signal routing uses to catch a mis-generation.
    """
    rover, arm = schemas
    xml = f'<root schema_location="{rover}"><Grasp/></root>'
    ok, matched, _ = validate_any([rover, arm], xml)
    assert ok is True
    assert matched == arm


def test_fails_against_every_schema(schemas):
    rover, arm = schemas
    xml = f'<root schema_location="{rover}"><Fly/></root>'
    ok, matched, error = validate_any([rover, arm], xml)
    assert ok is False
    assert matched is None
    assert "Fly" in error


def test_declaring_an_unavailable_schema_is_explained(schemas):
    rover, arm = schemas
    xml = '<root schema_location="schemas/does_not_exist.xsd"><Fly/></root>'
    ok, _, error = validate_any([rover, arm], xml)
    assert ok is False
    assert "does_not_exist.xsd" in error
    assert "not one of the available schemas" in error


def test_missing_schema_location_still_validates(schemas):
    """A plan with no declaration is not automatically rejected."""
    rover, arm = schemas
    xml = "<root><Drive/></root>"
    ok, matched, _ = validate_any([rover, arm], xml)
    # No schema_location attribute means the required attribute is absent, so
    # it should fail -- but by validation, not by an unhandled KeyError.
    assert ok is False
    assert matched is None


def test_empty_schema_list(schemas):
    ok, matched, error = validate_any([], "<root/>")
    assert ok is False
    assert matched is None
    assert "No schemas" in error


def test_schema_cache_survives_repeated_validation(schemas):
    """Repeated validation is safe (the compiled XSD is cached by mtime)."""
    rover, arm = schemas
    xml = f'<root schema_location="{rover}"><Drive/></root>'
    for _ in range(5):
        ok, matched, _ = validate_any([rover, arm], xml)
        assert ok is True and matched == rover
    assert validate_output(rover, xml)[0] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
