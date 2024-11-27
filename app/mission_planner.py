from typing import Tuple
import logging
import tempfile

import click
from lxml import etree
import yaml

from gpt_interface import GPTInterface
from network_interface import NetworkInterface


class MissionPlanner:
    def __init__(
        self,
        token_path: str,
        schema_path: str,
        farm_layout: str,
        max_retries: int,
        max_tokens: int,
        temperature: float,
        log_directory: str,
        logger: logging.Logger,
    ):
        # logger instance
        self.logger: logging.Logger = logger
        # set schema and farm file paths
        self.schema_path: str = schema_path
        self.farm_layout: str = farm_layout
        # logging GPT output folder
        self.log_directory: str = log_directory
        # max number of times that GPT can try and fix the mission plan
        self.max_retries: int = max_retries
        # init gpt interface
        self.gpt: GPTInterface = GPTInterface(
            self.logger, token_path, max_tokens, temperature
        )
        self.gpt.init_context(self.schema_path, self.farm_layout)

    def configure_network(self, host: str, port: int) -> None:
        # network interface
        self.nic: NetworkInterface = NetworkInterface(self.logger, host, port)
        # start connection to ROS agent
        self.nic.init_socket()

    def parse_xml(self, mp_out: str | None) -> str:
        assert isinstance(mp_out, str)

        xml_response: str = mp_out.split("```xml\n")[1]
        xml_response = xml_response.split("```")[0]

        return xml_response

    def write_out_xml(self, mp_out: str) -> str:
        # Create a temporary file in the specified directory
        with tempfile.NamedTemporaryFile(
            dir=self.log_directory, delete=False, mode="w"
        ) as temp_file:
            temp_file.write(mp_out)
            # name of temp file output
            temp_file_name = temp_file.name

        return temp_file_name

    def validate_output(self, xml_file: str) -> Tuple[bool, str]:
        try:
            # Parse the XSD file
            with open(self.schema_path, "rb") as schema_file:
                schema_root = etree.XML(schema_file.read())
            schema = etree.XMLSchema(schema_root)

            # Parse the XML file
            with open(xml_file, "rb") as fp:
                xml_doc = etree.parse(fp)

            # Validate the XML file against the XSD schema
            schema.assertValid(xml_doc)
            self.logger.debug("XML input from ChatGPT has been validated...")
            return True, "XML is valid."

        except etree.XMLSchemaError as e:
            return False, "XML is invalid: " + str(e)
        except Exception as e:
            return False, "An error occurred: " + str(e)

    def run(self) -> None:
        while True:
            # ask user for their mission plan
            mp_input: str = input("Enter the specifications for your mission plan: ")
            mp_out: str | None = self.gpt.ask_gpt(mp_input, True)
            self.logger.debug(mp_out)
            mp_out = self.parse_xml(mp_out)
            output_path = self.write_out_xml(mp_out)
            self.logger.debug(f"GPT output written to {output_path}...")
            ret, e = self.validate_output(output_path)

            if not ret:
                retry: int = 0
                while not ret and retry < self.max_retries:
                    self.logger.debug(
                        f"Retrying after failed to validate GPT mission plan: {e}"
                    )
                    mp_out = self.gpt.ask_gpt(e, True)
                    mp_out = self.parse_xml(mp_out)
                    output_path = self.write_out_xml(mp_out)
                    self.logger.debug(f"Temp GPT output written to {output_path}...")
                    ret, e = self.validate_output(output_path)
                    retry += 1
            # TODO: should we do this after every mission plan or leave them in context?
            self.gpt.reset_context()

            if not ret:
                self.logger.error("Unable to generate mission plan from your prompt...")
            else:
                # TODO: send off mission plan to TCP client
                self.nic.send_file(output_path)
                self.logger.debug("Successful mission plan generation...")

        # TODO: decide how the reuse flow works
        self.nic.close_socket()


@click.command()
@click.option(
    "--config",
    default="./app/config/localhost.yaml",
    help="YAML config file",
)
def main(config: str):
    with open(config, "r") as file:
        config_yaml: dict = yaml.safe_load(file)

    try:
        # configure logger
        logging.basicConfig(level=logging._nameToLevel[config_yaml["logging"]])
        logger: logging.Logger = logging.getLogger()

        mp: MissionPlanner = MissionPlanner(
            config_yaml["token"],
            config_yaml["schema"],
            config_yaml["farm_layout"],
            config_yaml["max_retries"],
            config_yaml["max_tokens"],
            config_yaml["temperature"],
            config_yaml["log_directory"],
            logger,
        )
        mp.configure_network(config_yaml["host"], int(config_yaml["port"]))
    except yaml.YAMLError as exc:
        logger.error(f"Improper YAML config: {exc}")

    mp.run()


if __name__ == "__main__":
    main()
