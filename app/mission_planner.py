import logging
import tempfile
import subprocess
import os
import stat
from typing import Tuple

import click
import yaml
import spot

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
            # configure spot
            spot.setup()

    def configure_network(self, host: str, port: int) -> None:
        # network interface
        self.nic: NetworkInterface = NetworkInterface(self.logger, host, port)
        # start connection to ROS agent
        self.nic.init_socket()

    def run(self) -> None:
        while True:
            # ask user for their mission plan
            mp_input: str = input("Enter the specifications for your mission plan: ")

            ret, e, mp_out = self._xml_generation(mp_input)

            # TODO: should we do this after every mission plan or leave them in context?
            self.gpt.reset_context()

            if ret:
                self.logger.info("Successful mission plan generation...")
                file_mp_out = self._write_out_file(mp_out)
            else:
                self.logger.error(
                    f"Unable to generate mission plan from your prompt... error: {e}"
                )
                return

            # if specified in the YAM config to formally verify
            if self.ltl:
                ret = self._formal_verification(mp_input, mp_out)

            if ret:
                # TODO: send off mission plan to TCP client
                self.nic.send_file(file_mp_out)
                self.logger.debug("Successful mission plan generation...")
            else:
                self.logger.error("Unable to formally verify from your prompt...")
                # TODO: do we break here?

        # TODO: decide how the reuse flow works
        self.nic.close_socket()

    def get_promela_output_path(self) -> str:
        return self.promela_path

    def _formal_verification(self, mission_query: str, xml_mp: str) -> bool:
        ret: bool = False

        self.logger.debug("Generating Promela from mission...")
        # from the mission output, create an XML tree
        self.promela.init_xml_tree(xml_mp)
        # generate promela string that defines mission/system
        promela_string: str = self.promela.parse_xml()
        # TODO: we need to understand if this is creating bias
        task_names: str = self.promela.get_task_names()
        globals: str = self.promela.get_globals()

        # this begins the second phase of the formal verification
        self.logger.debug(
            "Generating LTL to verify mission against Promela generated system..."
        )

        # TODO: does this introduce bias?
        self.pml_gpt.add_context(
            "You MUST use these Promela object names when generating the LTL. Otherwise syntax will be incorrect and SPIN will fail: "
            + task_names
            + "\n"
            + globals
        )
        # self.pml_gpt.add_context(
        #     "This is the Promela model that you should generate your LTL based on: "
        #     + promela_string
        # )

        if self._ltl_generation(mission_query, promela_string) == 0:
            self.logger.info("Please confirm validity of mission decomposition...")
        else:
            self.logger.error(
                "Failed to validate mission... Please see Promela model and trail."
            )
            return ret

        spot_out: str | None = self.pml_gpt.ask_gpt(
            'Now please take that last LTL and convert it to SPOT for me. \
            This means no comparators (<>==), only atomic prepositions. Also, make their names meaningful for the user to read. \
            All chained conditional statements must either be one or the other i.e. temp > 30 or temp <= 30. \
            Name these conditional atomic preps the same but negate for opposite case when possible, instead of a new name. \
            Account for this in your new LTL. \
            Just return the formula for SPOT, no "ltl { <ltl here> }". Example: \
            ```ltl \
            <>(MoveToTree1 &&  \
            X(TakeTemperatureSample1 &&  \
            X((highTemp -> X(MoveToTree2)) && \
            X(MoveToEndTree)))) \
            ```',
            True,
        )

        spot_out = parse_xml(spot_out, "ltl")

        aut = spot.translate(spot_out)

        aut.save("spot.aut", append=False)

        ac = aut.accepting_run()

        self.logger.debug(ac)

        explanation: str | None = self.pml_gpt.ask_gpt(
            " \
            Explain the accepting run states simply for anyone to understand it based on the mission I originally asked, ignoring the cycle. \
            Do NOT say anything other than the explanation of tasks. \
            Speak in future tense. For example: \
            The robot is set to perform the following tasks: \
                \
            1. First, the robot will move to the first tree. \
            2. Then, it will take a temperature sample at the first tree. \
            3. If the temperature at the first tree is high (over 30°C), the robot will move to the second tree. \
            4. If the tempature is low, the robot will go straight to the end tree.\
                \
            Does this explanation accurately reflect the mission you intended? \
            \n"
            + str(ac)
        )
        assert isinstance(explanation, str)

        resp: str = ""
        while resp != ("y" or "n"):
            resp = input(
                explanation
                + "\nNote, this is just one of several possibilities. \n\nType y/n."
            )

            if resp == "y":
                self.logger.info("Mission proceeding...")
                ret = True
                break
            elif resp == "n":
                self.logger.info(
                    "Conflict between mission and validator... Let's try again."
                )
                break
            else:
                continue

        # get rid of previous promela system
        self.pml_gpt.reset_context()

        return ret

    def _xml_generation(self, mp_input: str) -> Tuple[bool, str, str]:
        retry: int = -1
        ret: bool = False
        prompt: str = mp_input

        while not ret and retry < self.max_retries:
            # ask mission with relevant context
            mp_out: str | None = self.gpt.ask_gpt(prompt, True)
            # if you're in debug mode, write the whole answer, not just xml
            if self.debug:
                self._write_out_file(mp_out)
                self.logger.debug(mp_out)
            # XML should be formatted ```xml```
            mp_out = parse_xml(mp_out)
            # path to selected schema based on xsi:schemaLocation
            selected_schema: str = parse_schema_location(mp_out)
            self.logger.debug(f"Schema selected by GPT: {selected_schema}")
            # validate mission based on XSD
            ret, e = validate_output(selected_schema, mp_out)
            retry += 1
            if not ret:
                prompt = (
                    "I got this error on validation. Please fix and return to me the full XML mission plan: \n"
                    + e
                )
                self.logger.debug(
                    f"Retrying after failed to validate GPT mission plan: {e}"
                )

        assert isinstance(mp_out, str)
        return ret, e, mp_out

    def _ltl_generation(self, mission_query: str, promela_string: str) -> int:
        retry: int = -1
        ret: int = 0
        prompt: str = "Generate SPIN LTL based on this mission plan: " + mission_query

        while retry < self.max_retries:
            # use second GPT agent to generate LTL
            ltl_out: str | None = self.pml_gpt.ask_gpt(prompt, True)
            # parse out LTL statement
            ltl_out = parse_xml(ltl_out, "ltl")
            # append to promela file
            new_promela_string: str = promela_string + "\n" + ltl_out
            # write pml system and LTL to file
            self.promela_path = self._write_out_file(new_promela_string)
            # execute spin verification
            # TODO: this output isn't as useful as trail file, maybe can use later if needed.
            ret, err = self._execute_shell_cmd(
                [self.spin_path, "-search", "-a", "-O2", self.promela_path]
            )
            # if you didn't get an error from validation step, no more retries
            if ret != 0:
                self.logger.error(f"Failed to execute spin command with error: {err}")
                break
            pml_file: str = self.promela_path.split("/")[-1]
            # trail file means you failed
            if os.path.isfile(pml_file + ".trail"):
                # move trail file since the promela file gets sent to self.log_directory
                os.replace(pml_file + ".trail", self.promela_path + ".trail")
                # run trail
                ret, trail_out = self._execute_shell_cmd(
                    [self.spin_path, "-t", self.promela_path]
                )
                if ret != 0:
                    self.logger.error(
                        f"Failed to execute trail file... Unable to get trace: {trail_out}"
                    )
                    break
                prompt = (
                    "Failure occured in SPIN validation output. Generate a new LTL: \n"
                    + trail_out
                )
                self.logger.debug(
                    "Retrying after failing to pass formal validation step..."
                )
            # no trail file, success
            else:
                break

            retry += 1

        return ret

    def _execute_shell_cmd(self, command: list) -> Tuple[int, str]:
        ret: int = 0
        out: str = ""

        try:
            out = str(subprocess.check_output(command))
        except subprocess.CalledProcessError as err:
            ret = err.returncode
            out = str(err.output)

        return ret, out

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
        # OpenAI loggers turned off completely.
        logging.getLogger("openai").setLevel(logging.CRITICAL)
        logging.getLogger("httpx").setLevel(logging.CRITICAL)
        logging.getLogger("httpcore").setLevel(logging.CRITICAL)
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
