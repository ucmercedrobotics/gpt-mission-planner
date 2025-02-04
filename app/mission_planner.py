import logging
import tempfile
import subprocess
import os
import stat

import click
import yaml

from gpt_interface import GPTInterface
from network_interface import NetworkInterface
from xml_helper import parse_schema_location, parse_xml, validate_output
from promela_compiler import PromelaCompiler


LTL_KEY: str = "ltl"
PROMELA_TEMPLATE_KEY: str = "promela_template"
SPIN_PATH_KEY: str = "spin_path"


class MissionPlanner:
    def __init__(
        self,
        token_path: str,
        schema_paths: list[str],
        context_files: list[str],
        max_retries: int,
        max_tokens: int,
        temperature: float,
        ltl: bool,
        promela_template_path: str,
        spin_path: str,
        log_directory: str,
        logger: logging.Logger,
        debug: bool,
    ):
        # logger instance
        self.logger: logging.Logger = logger
        # debug mode
        self.debug: bool = debug
        # set schema and farm file paths
        self.schema_paths: list[str] = schema_paths
        self.context_files: list[str] = context_files
        # logging GPT output folder, make if not there
        self.log_directory: str = log_directory
        os.makedirs(self.log_directory, mode=777, exist_ok=True)
        # max number of times that GPT can try and fix the mission plan
        self.max_retries: int = max_retries
        # init gpt interface
        self.gpt: GPTInterface = GPTInterface(
            self.logger, token_path, max_tokens, temperature
        )
        self.gpt.init_context(self.schema_paths, self.context_files)
        # init Promela compiler
        self.ltl: bool = ltl
        if self.ltl:
            # init XML mission gpt interface
            self.pml_gpt: GPTInterface = GPTInterface(
                self.logger, token_path, max_tokens, temperature
            )
            self.promela: PromelaCompiler = PromelaCompiler(
                promela_template_path, self.logger
            )
            self.pml_gpt.init_promela_context(
                self.schema_paths,
                self.promela.get_promela_template(),
                self.context_files,
            )
            # this string gets generated at a later time when promela is written out
            self.promela_path: str = ""
            self.spin_path: str = spin_path

    def configure_network(self, host: str, port: int) -> None:
        # network interface
        self.nic: NetworkInterface = NetworkInterface(self.logger, host, port)
        # start connection to ROS agent
        self.nic.init_socket()

    def run(self) -> None:
        while True:
            # ask user for their mission plan
            mp_input: str = input("Enter the specifications for your mission plan: ")
            # ask mission with relevant context
            mp_out: str | None = self.gpt.ask_gpt(mp_input, True)
            # if you're in debug mode, write the whole answer, not just xml
            if self.debug:
                self._write_out_file(mp_out)
                self.logger.debug(mp_out)
            # XML should be formatted ```xml```
            mp_out = parse_xml(mp_out)
            # write to temp file
            output_path = self._write_out_file(mp_out)
            self.logger.debug(f"GPT output written to {output_path}...")
            # path to selected schema based on xsi:schemaLocation
            selected_schema: str = parse_schema_location(output_path)
            ret, e = validate_output(selected_schema, output_path)
            self.logger.debug(f"Schema selected by GPT: {selected_schema}")

            if not ret:
                retry: int = 0
                while not ret and retry < self.max_retries:
                    self.logger.debug(
                        f"Retrying after failed to validate GPT mission plan: {e}"
                    )
                    # ask mission with relevant context
                    mp_out = self.gpt.ask_gpt(
                        e + "\n Please return to me the full XML mission plan.", True
                    )
                    # XML should be formatted ```xml```
                    mp_out = parse_xml(mp_out)
                    # write to temp file
                    output_path = self._write_out_file(mp_out)
                    self.logger.debug(f"Temp GPT output written to {output_path}...")
                    # validate mission based on XSD
                    ret, e = validate_output(selected_schema, output_path)
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

    def _formal_verification(self, mission_query: str, xml_mp_path: str) -> None:
        self.logger.info("Generating Promela from mission...")
        # from the mission output, create an XML tree
        self.promela.init_xml_tree(xml_mp_path)
        # generate promela string that defines mission/system
        promela_string: str = self.promela.parse_xml()
        task_names: str = self.promela.get_task_names()

        # this begins the second phase of the formal verification
        self.logger.info(
            "Generating LTL to verify mission against Promela generated system..."
        )

        self.pml_gpt.add_context(
            "Use these Promela object names when generating the LTL so the syntax matche the system file: "
            + task_names
        )
        # TODO: think about adding global variables for more asserts

        # use second GPT agent to generate LTL
        ltl_out: str | None = self.pml_gpt.ask_gpt(
            "Mission plan: " + mission_query, True
        )
        # parse out LTL statement
        ltl_out = parse_xml(ltl_out, "ltl")
        # append to promela file
        promela_string += "\n" + ltl_out
        # write pml system and LTL to file
        self.promela_path = self._write_out_file(promela_string)
        # execute spin verification
        try:
            self.logger.info(
                subprocess.check_output(
                    [self.spin_path, "-search", "-a", self.promela_path]
                )
            )
        except subprocess.CalledProcessError as err:
            self.logger.error(err)
        # TODO: figure out if validation was successful, retry if not

        # get rid of previous promela system
        self.pml_gpt.reset_context()

    def get_promela_output_path(self) -> str:
        return self.promela_path

    def _write_out_file(self, mp_out: str | None) -> str:
        assert isinstance(mp_out, str)

        # Create a temporary file in the specified directory
        with tempfile.NamedTemporaryFile(
            dir=self.log_directory, delete=False, mode="w"
        ) as temp_file:
            temp_file.write(mp_out)
            # name of temp file output
            temp_file_name = temp_file.name

        os.chmod(temp_file_name, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

        return temp_file_name


@click.command()
@click.option(
    "--config",
    default="./app/config/localhost.yaml",
    help="YAML config file",
)
def main(config: str):
    with open(config, "r") as file:
        config_yaml: dict = yaml.safe_load(file)

    context_files: list[str] = []

    # don't generate/check LTL by default
    ltl: bool = False
    pml_template_path: str = ""
    spin_path: str = ""

    try:
        # configure logger
        logging.basicConfig(level=logging._nameToLevel[config_yaml["logging"]])
        logger: logging.Logger = logging.getLogger()

        if "context_files" in config_yaml:
            context_files = config_yaml["context_files"]
        else:
            logger.info("No additional context files found. Proceeding...")

        # if user specifies config key -> optional keys
        if (
            LTL_KEY in config_yaml
            and PROMELA_TEMPLATE_KEY in config_yaml
            and SPIN_PATH_KEY in config_yaml
        ):
            ltl = config_yaml[LTL_KEY]
            pml_template_path = config_yaml[PROMELA_TEMPLATE_KEY]
            spin_path = config_yaml[SPIN_PATH_KEY]
        else:
            logger.warning(
                "No spin configuration found. Proceeding without formal verification..."
            )

        mp: MissionPlanner = MissionPlanner(
            config_yaml["token"],
            config_yaml["schema"],
            context_files,
            config_yaml["max_retries"],
            config_yaml["max_tokens"],
            config_yaml["temperature"],
            ltl,
            pml_template_path,
            spin_path,
            config_yaml["log_directory"],
            logger,
            config_yaml["debug"],
        )
        mp.configure_network(config_yaml["host"], int(config_yaml["port"]))
    except yaml.YAMLError as exc:
        logger.error(f"Improper YAML config: {exc}")

    mp.run()


if __name__ == "__main__":
    main()
