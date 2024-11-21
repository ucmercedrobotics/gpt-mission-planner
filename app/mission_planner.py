from typing import Tuple
import logging
import tempfile
import subprocess
import re

import click
from lxml import etree
import yaml

from gpt_interface import GPTInterface
from network_interface import NetworkInterface
from promela_compiler import PromelaCompiler


LTL_KEY: str = "ltl"
PROMELA_TEMPLATE_KEY: str = "promela_template"
SPIN_PATH_KEY: str = "spin_path"


class MissionPlanner:
    def __init__(
        self,
        token_path: str,
        schema_path: str,
        farm_layout: str,
        max_retries: int,
        max_tokens: int, 
        temperature: float,
        ltl: bool,
        promela_template_path: str|None,
        spin_path: str|None,
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
        # init XML mission gpt interface
        self.gpt: GPTInterface = GPTInterface(self.logger, token_path, max_tokens, temperature)
        self.gpt.init_xml_mp_context(self.schema_path, self.farm_layout)
        # init Promela compiler
        self.ltl: bool = ltl
        if self.ltl:
            # init XML mission gpt interface
            self.pml_gpt: GPTInterface = GPTInterface(self.logger, token_path, max_tokens, temperature)
            self.pml_gpt.init_promela_context(self.schema_path, promela_template_path, self.farm_layout)
            self.promela: PromelaCompiler = PromelaCompiler(self.pml_gpt.get_promela_template(), self.logger)
            # this string gets generated at a later time when promela is written out
            self.promela_path: str|None = None
            self.spin_path: str = spin_path

    def configure_network(self, host: str, port: int) -> None:
        # network interface
        self.nic: NetworkInterface = NetworkInterface(self.logger, host, port)
        # start connection to ROS agent
        self.nic.init_socket()

    def run(self):
        while True:
            # ask user for their mission plan
            mp_input: str = input("Enter the specifications for your mission plan: ")
            mp_out: str = self.gpt.ask_gpt(mp_input, True)
            self.logger.debug(mp_out)
            mp_out = self._parse_gpt_code(mp_out)
            output_path = self._write_out_temp_file(mp_out)
            self.logger.debug(f"GPT output written to {output_path}...")
            ret, e = self._validate_output(output_path)

            if not ret:
                retry: int = 0
                while not ret and retry < self.max_retries:
                    self.logger.debug(f"Retrying after failed to validate GPT mission plan: {e}")
                    mp_out = self.gpt.ask_gpt(e, True)
                    mp_out = self._parse_gpt_code(mp_out)
                    output_path = self._write_out_temp_file(mp_out)
                    self.logger.debug(f"Temp GPT output written to {output_path}...")
                    ret, e = self._validate_output(output_path)
                    retry += 1
            # TODO: should we do this after every mission plan or leave them in context?
            self.gpt.reset_context()

            if not ret:
                self.logger.error("Unable to generate mission plan from your prompt...")
            else:
                self.logger.debug("Successful mission plan generation...")

            # if specified in the YAM config to formally verify
            if self.ltl:
                self._formal_verification(mp_input, output_path)

            if not ret:
                self.logger.error("Unable to formally verify from your prompt...")
            else:
                # TODO: send off mission plan to TCP client
                self.nic.send_file(output_path)
                self.logger.debug("Successful mission plan generation...")
            
        # TODO: decide how the reuse flow works
        self.nic.close_socket()

    def _parse_gpt_code(self, mp_out: str, token: str = "xml") -> str:
        xml_response: str = mp_out.split("```" + token + "\n")[1]
        xml_response = xml_response.split("```")[0]

        return xml_response

    def _write_out_temp_file(self, text: str) -> str:
        # Create a temporary file in the specified directory
        with tempfile.NamedTemporaryFile(dir=self.log_directory, delete=False, mode="w") as temp_file:
            temp_file.write(text)
            # name of temp file output
            temp_file_name = temp_file.name
        
        return temp_file_name

    def _validate_output(self, xml_file: str) -> Tuple[bool, str]:
        try:
            # Parse the XSD file
            with open(self.schema_path, "rb") as schema_file:
                schema_root = etree.XML(schema_file.read())
            schema = etree.XMLSchema(schema_root)

            # Parse the XML file
            with open(xml_file, 'rb') as xml_file:
                xml_doc = etree.parse(xml_file)

            # Validate the XML file against the XSD schema
            schema.assertValid(xml_doc)
            self.logger.debug("XML input from ChatGPT has been validated...")
            return True, "XML is valid."

        except etree.XMLSchemaError as e:
            return False, "XML is invalid: " + str(e)
        except Exception as e:
            return False, "An error occurred: " + str(e)

    def _formal_verification(self, mission_query: str, xml_mp_path: str) -> None:
        self.logger.info("Generating Promela from mission...")
        # from the mission output, create an XML tree
        self.promela.init_xml_tree(xml_mp_path)
        # generate promela string that defines mission/system
        promela_string: str = self.promela.parse_xml()

        # this begins the second phase of the formal verification
        self.logger.info("Generating LTL to verify mission against Promela generated system...")

        # use second GPT agent to generate LTL
        ltl_out: str = self.pml_gpt.ask_gpt(mission_query, True)
        # parse out LTL statement
        ltl_out = self._parse_gpt_code(ltl_out, "ltl")
        # append to promela file
        promela_string += "\n" + ltl_out
        # write pml system and LTL to file
        self.promela_path: str = self._write_out_temp_file(promela_string)
        # execute spin verification
        try:
            # NOTE: this command doesn't produce the trail required for feedback to GPT, though it isn't very useful
            spin_out: str = subprocess.check_output([self.spin_path, "-search", "-n", self.promela_path])
            self.logger.info(spin_out)
        except subprocess.CalledProcessError as err:
            self.logger.error(err)

        errors: int = self._spin_regex(spin_out)
        if errors > 0:
            self.logger.error(f"Functional model fails never claim {errors} times...")
            # TODO: redo with relevant information

    def _spin_regex(self, spin_out: str) -> int:
        errors: int = 0

        match = re.search(r"errors:\s*(\d+)", spin_out)

        # if the output matches the normal format, get the number of errors
        if match:
            errors = match.group(1)
        # if this happens that probably means that the output was bad...
        else:
            errors = -1

        return errors


@click.command()
@click.option(
    "--config",
    default="./app/config/localhost.yaml",
    help="YAML config file",
)
def main(config: str):
    with open(config, "r") as file:
        config_yaml: yaml.Node = yaml.safe_load(file)

    # don't generate/check LTL by default
    ltl: bool = False
    pml_template_path: str|None = None
    spin_path: str|None = None

    try:
        # configure logger
        logging.basicConfig(level=logging._nameToLevel[config_yaml["logging"]])
        logger: logging.Logger = logging.getLogger()

        # if user specifies config key -> optional keys
        if LTL_KEY in config_yaml and PROMELA_TEMPLATE_KEY in config_yaml and SPIN_PATH_KEY in config_yaml:
            ltl = config_yaml[LTL_KEY]
            pml_template_path = config_yaml[PROMELA_TEMPLATE_KEY]
            spin_path = config_yaml[SPIN_PATH_KEY]

        mp: MissionPlanner = MissionPlanner(
            config_yaml["token"],
            config_yaml["schema"],
            config_yaml["farm_layout"],
            config_yaml["max_retries"],
            config_yaml["max_tokens"],
            config_yaml["temperature"],
            ltl,
            pml_template_path,
            spin_path,
            config_yaml["log_directory"],
            logger,
        )
        mp.configure_network(config_yaml["host"], int(config_yaml["port"]))
    except yaml.YAMLError as exc:
        logger.error(f"Improper YAML config: {exc}")

    mp.run()


if __name__ == "__main__":
    main()
