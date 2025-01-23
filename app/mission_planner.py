import logging
import tempfile

import click
import yaml

from gpt_interface import GPTInterface
from network_interface import NetworkInterface
from xml_helper import parse_schema_location, parse_xml, validate_output


class MissionPlanner:
    def __init__(
        self,
        token_path: str,
        schema_paths: list[str],
        context_files: list[str],
        max_retries: int,
        max_tokens: int,
        temperature: float,
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
        # logging GPT output folder
        self.log_directory: str = log_directory
        # max number of times that GPT can try and fix the mission plan
        self.max_retries: int = max_retries
        # init gpt interface
        self.gpt: GPTInterface = GPTInterface(
            self.logger, token_path, max_tokens, temperature
        )
        self.gpt.init_context(self.schema_paths, self.context_files)

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
                # TODO: send off mission plan to TCP client
                self.nic.send_file(output_path)
                self.logger.debug("Successful mission plan generation...")

        # TODO: decide how the reuse flow works
        self.nic.close_socket()

    def _write_out_file(self, mp_out: str | None) -> str:
        assert isinstance(mp_out, str)

        # Create a temporary file in the specified directory
        with tempfile.NamedTemporaryFile(
            dir=self.log_directory, delete=False, mode="w"
        ) as temp_file:
            temp_file.write(mp_out)
            # name of temp file output
            temp_file_name = temp_file.name

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

    try:
        # configure logger
        logging.basicConfig(level=logging._nameToLevel[config_yaml["logging"]])
        logger: logging.Logger = logging.getLogger()

        if "context_files" in config_yaml:
            context_files = config_yaml["context_files"]
        else:
            logger.info("No additional context files found. Proceeding...")

        mp: MissionPlanner = MissionPlanner(
            config_yaml["token"],
            config_yaml["schema"],
            context_files,
            config_yaml["max_retries"],
            config_yaml["max_tokens"],
            config_yaml["temperature"],
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
