from typing import Tuple

from lxml import etree

from app.xml_types import AttributeTags, ControlTags, ActionTags


def parse_schema_location(xml_mp: str) -> str:
    root: etree._Element = etree.fromstring(xml_mp)
    location = root.attrib[AttributeTags.SchemaLocation]
    return location


def parse_code(mp_out: str | None, code_type: str = "xml") -> str:
    assert isinstance(mp_out, str)

    xml_response: str = mp_out.split("```" + code_type + "\n")[1]
    xml_response = xml_response.split("```")[0]

    return xml_response


def validate_output(schema_path: str, xml_mp: str) -> Tuple[bool, str]:
    try:
        # Parse the XSD file
        with open(schema_path, "rb") as schema_file:
            schema_root = etree.XML(schema_file.read())
        schema = etree.XMLSchema(schema_root)

        # Parse the XML file
        root: etree._Element = etree.fromstring(xml_mp)

        # Validate the XML file against the XSD schema
        schema.assertValid(root)
        return True, "XML is valid."

    except etree.XMLSchemaError as e:
        return False, "XML is invalid: " + str(e)
    except Exception as e:
        return False, "An error occurred: " + str(e)


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
