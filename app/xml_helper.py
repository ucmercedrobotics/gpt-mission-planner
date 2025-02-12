from typing import Tuple

from lxml import etree


SCHEMA_LOCATION_TAG: str = "schemaLocation"
SCHEMA_LOCATION_INDEX: int = 1
XSI: str = "xsi"


def parse_schema_location(xml_file: str) -> str:
    root: etree._Element = etree.fromstring(xml_file)
    xsi = root.nsmap[XSI]
    location = root.attrib["{" + xsi + "}" + SCHEMA_LOCATION_TAG]
    return location.split(root.nsmap[None] + " ")[SCHEMA_LOCATION_INDEX]


def parse_xml(mp_out: str | None, code_type: str = "xml") -> str:
    assert isinstance(mp_out, str)

    xml_response: str = mp_out.split("```" + code_type + "\n")[1]
    xml_response = xml_response.split("```")[0]

    return xml_response


def validate_output(schema_path: str, xml_file: str) -> Tuple[bool, str]:
    try:
        # Parse the XSD file
        with open(schema_path, "rb") as schema_file:
            schema_root = etree.XML(schema_file.read())
        schema = etree.XMLSchema(schema_root)

        # Parse the XML file
        xml_doc: etree._Element = etree.fromstring(xml_file)

        # Validate the XML file against the XSD schema
        schema.assertValid(xml_doc)
        return True, "XML is valid."

    except etree.XMLSchemaError as e:
        return False, "XML is invalid: " + str(e)
    except Exception as e:
        return False, "An error occurred: " + str(e)
