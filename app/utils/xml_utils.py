from typing import Tuple

from lxml import etree


SCHEMA_LOCATION_TAG: str = "schema_location"


def parse_schema_location(xml_mp: str) -> str:
    root: etree._Element = etree.fromstring(xml_mp)
    location = root.attrib[SCHEMA_LOCATION_TAG]
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
    bt: etree._Element = root.find("BehaviorTree")

    return task_count


def count_consecutive_tags(parent, tag_name):
    count = 0
    prev_was_target = False

    for elem in parent:
        if elem.tag == tag_name:
            if prev_was_target:
                count += 1  # Count only the first occurrence of a sequence
                prev_was_target = False  # Reset so we count each cluster once
            else:
                prev_was_target = True
        else:
            prev_was_target = False  # Reset when a different tag is encountered

        # Recursively check nested elements
        count += count_consecutive_tags(elem, tag_name)

    return count
