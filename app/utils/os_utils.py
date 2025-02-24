from typing import Tuple
import tempfile
import subprocess
import os
import stat

from lxml import etree


SCHEMA_LOCATION_TAG: str = "schemaLocation"
SCHEMA_LOCATION_INDEX: int = 1
XSI: str = "xsi"


def parse_schema_location(xml_file: str) -> str:
    root: etree._Element = etree.fromstring(xml_file)
    xsi = root.nsmap[XSI]
    location = root.attrib["{" + xsi + "}" + SCHEMA_LOCATION_TAG]
    return location.split(root.nsmap[None] + " ")[SCHEMA_LOCATION_INDEX]


def parse_code(mp_out: str | None, code_type: str = "xml") -> str:
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


def execute_shell_cmd(command: list) -> Tuple[int, str]:
    ret: int = 0
    out: str = ""

    try:
        out = str(subprocess.check_output(command))
    except subprocess.CalledProcessError as err:
        ret = err.returncode
        out = str(err.output)

    return ret, out


def write_out_file(dir: str, mp_out: str | None) -> str:
    assert isinstance(mp_out, str)

    # Create a temporary file in the specified directory
    with tempfile.NamedTemporaryFile(dir=dir, delete=False, mode="w") as temp_file:
        temp_file.write(mp_out)
        # name of temp file output
        temp_file_name = temp_file.name

    os.chmod(temp_file_name, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

    return temp_file_name
