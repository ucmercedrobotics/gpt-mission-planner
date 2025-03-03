from typing import Tuple

from lxml import etree


SCHEMA_LOCATION_TAG: str = "schemaLocation"
SCHEMA_LOCATION_INDEX: int = 1
XSI: str = "xsi"
# TODO: extract this from XSD.
NS: dict = {
    "xs": "http://www.w3.org/2001/XMLSchema",
    "task": "https://robotics.ucmerced.edu/task",
}


def parse_schema_location(xml_mp: str) -> str:
    root: etree._Element = etree.fromstring(xml_mp)
    xsi = root.nsmap[XSI]
    location = root.attrib["{" + xsi + "}" + SCHEMA_LOCATION_TAG]
    return location.split(root.nsmap[None] + " ")[SCHEMA_LOCATION_INDEX]


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
    action_sequence: etree._Element = root.find("task:ActionSequence", NS)

    if action_sequence is not None:
        seq = action_sequence.find("task:Sequence", NS)
        if seq is not None:
            task_count = len(seq.xpath(".//task:TaskID", namespaces=NS))
            task_count += len(seq.xpath(".//task:ConditionalActions", namespaces=NS))

    return task_count
