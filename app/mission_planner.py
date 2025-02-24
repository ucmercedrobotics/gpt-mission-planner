import logging
import os
from typing import Tuple

import click
import yaml
import spot

from gpt_interface import LLMInterface
from network_interface import NetworkInterface
from utils.os_utils import (
    parse_schema_location,
    parse_code,
    validate_output,
    execute_shell_cmd,
    write_out_file,
)
from promela_compiler import PromelaCompiler
from context import SPOT_CONTEXT
from utils.spot_utils import generate_accepting_run_string


LTL_KEY: str = "ltl"
PROMELA_TEMPLATE_KEY: str = "promela_template"
SPIN_PATH_KEY: str = "spin_path"
CHATGPT4O: str = "openai:gpt-4o"
CLAUDE35: str = "anthropic:claude-3-5-sonnet-20241022"
# TODO: remove this
HUMAN_REVIEW: bool = False
EXAMPLE_RUNS: int = 3


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
        self.gpt: LLMInterface = LLMInterface(
            self.logger, token_path, CHATGPT4O, max_tokens, temperature
        )
        self.gpt.init_context(self.schema_paths, self.context_files)
        # init Promela compiler
        self.ltl: bool = ltl
        if self.ltl:
            self.human_review: bool = HUMAN_REVIEW
            # init XML mission gpt interface
            self.pml_gpt: LLMInterface = LLMInterface(
                self.logger, token_path, CHATGPT4O, max_tokens, temperature
            )
            # Claude human verification substitute
            self.verification_checker: LLMInterface = LLMInterface(
                self.logger, token_path, CLAUDE35, max_tokens, temperature
            )
            # object for compiling Promela from XML
            self.promela: PromelaCompiler = PromelaCompiler(
                promela_template_path, self.logger
            )
            # setup context to give to formal verification agent.
            # NOTE: only schemas and template used for now
            self.pml_gpt.init_promela_context(
                self.schema_paths,
                self.promela.get_promela_template(),
                self.context_files,
            )
            # this string gets generated at a later time when promela is written out
            self.promela_path: str = ""
            # spin binary location
            self.spin_path: str = spin_path
            # configure spot
            spot.setup()

    def configure_network(self, host: str, port: int) -> None:
        # network interface
        self.nic: NetworkInterface = NetworkInterface(self.logger, host, port)
        # start connection to ROS agent
        self.nic.init_socket()

    def get_promela_output_path(self) -> str:
        return self.promela_path

    def run(self) -> None:
        while True:
            # ask user for their mission plan
            mp_input: str = input("Enter the specifications for your mission plan: ")
            # loop for asking LLM for XML mission plan based on input -> handles failure retries
            ret, e, mp_out = self._xml_generation(mp_input)
            # TODO: should we do this after every mission plan or leave them in context?
            self.gpt.reset_context()
            # check if we have a valid XML
            if ret:
                self.logger.info("Successful mission plan generation...")
                file_mp_out = write_out_file(self.log_directory, mp_out)
            else:
                self.logger.error(
                    f"Unable to generate mission plan from your prompt... error: {e}"
                )
                return
            # if specified in the YAML config to formally verify
            if self.ltl:
                ret = self._formal_verification(mp_input, mp_out)
            # failure of this will only occur if formal verification was enabled.
            # otherwise it sends out XML mission via TCP
            if ret:
                # TODO: send off mission plan to TCP client
                self.nic.send_file(file_mp_out)
                self.logger.debug(
                    f"Sending mission XML {file_mp_out} out to robot over TCP..."
                )
            else:
                self.logger.error("Unable to formally verify from your prompt...")
                # TODO: do we break here?

        # TODO: decide how the reuse flow works
        self.nic.close_socket()

    def _xml_generation(self, mp_input: str) -> Tuple[bool, str, str]:
        retry: int = -1
        ret: bool = False
        prompt: str = mp_input

        while not ret and retry < self.max_retries:
            # ask mission with relevant context
            mp_out: str | None = self.gpt.ask_gpt(prompt, True)
            # if you're in debug mode, write the whole answer, not just xml
            if self.debug:
                write_out_file(self.log_directory, mp_out)
                self.logger.debug(mp_out)
            # XML should be formatted ```xml```
            mp_out = parse_code(mp_out)
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

    def _formal_verification(self, mission_query: str, xml_mp: str) -> bool:
        ret: bool = False

        self.logger.debug("Generating Promela from mission...")
        # from the mission output, create an XML tree
        self.promela.init_xml_tree(xml_mp)
        # generate promela string that defines mission/system
        promela_string: str = self.promela.parse_code()
        # TODO: we need to understand if this is creating bias
        task_names: str = self.promela.get_task_names()
        globals: str = self.promela.get_globals()
        # this begins the second phase of the formal verification
        ask: str = (
            "You MUST use these Promela object names when generating the LTL. Otherwise syntax will be incorrect and SPIN will fail: "
            + "Tasks: \n"
            + task_names
            + "\n"
            + "Sample returns: \n"
            + globals
        )
        self.logger.debug(
            f"Generating LTL to verify mission against Promela generated system...\n Asking: {ask}"
        )
        # TODO: does this introduce bias?
        self.pml_gpt.add_context(ask)
        # self.pml_gpt.add_context(
        #     "This is the Promela model that you should generate your LTL based on: "
        #     + promela_string
        # )
        # generates the LTL and verifies it with SPIN; retry enabled
        if self._ltl_generation(mission_query, promela_string) == 0:
            self.logger.info("Please confirm validity of mission decomposition...")
            self.logger.debug(f"Promela description in file {self.promela_path}.")
        else:
            self.logger.error(
                "Failed to validate mission... Please see Promela model and trail."
            )
            return ret
        # ask LLM for a SPOT compliant LTL to visualize it
        # TODO: is this step necessary?
        spot_out: str | None = self.pml_gpt.ask_gpt(
            SPOT_CONTEXT,
            True,
        )

        spot_out = parse_code(spot_out, "ltl")

        aut = spot.translate(spot_out)

        aut.save("spot.aut", append=False)

        runs: list[str] = [
            generate_accepting_run_string(aut) for _ in range(EXAMPLE_RUNS)
        ]
        runs_str: str = "\n".join(runs)

        if self.human_review:
            resp: str = ""
            while resp != ("y" or "n"):
                resp = input(
                    "Here are 3 example executions of your mission: "
                    + runs_str
                    + "\nNote, these are just several possible runs. \n\nType y/n."
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
        else:
            ask = (
                'Please answer this with one word: "Yes" or "No". \
                Here is a mission plan request along with examples of how this mission would be carried out. \
                In your opinion, would you say that ALL of these examples are faithful to requested mission?\nMission request: \n'
                + mission_query
                + "\nExample runs:\n"
                + runs_str
            )
            self.logger.debug(f"Asking arbiter: {ask}")
            acceptance = self.verification_checker.ask_gpt(ask)
            assert isinstance(acceptance, str)

            self.logger.debug(f"Claude says {acceptance}")

            if "yes" in acceptance.lower():
                self.logger.info("Claude approves. Mission proceeding...")
                ret = True
            else:
                self.logger.warning(f"Claude disapproves. See example runs: {runs}")

        # get rid of previous promela system
        self.pml_gpt.reset_context()

        return ret

    def _ltl_generation(self, mission_query: str, promela_string: str) -> int:
        retry: int = -1
        ret: int = -1
        prompt: str = "Generate SPIN LTL based on this mission plan: " + mission_query

        while retry < self.max_retries:
            # use second GPT agent to generate LTL
            ltl_out: str | None = self.pml_gpt.ask_gpt(prompt, True)
            # parse out LTL statement
            ltl_out = parse_code(ltl_out, "ltl")
            assert isinstance(ltl_out, str)
            # append to promela file
            new_promela_string: str = promela_string + "\n" + ltl_out
            # write pml system and LTL to file
            self.promela_path = write_out_file(self.log_directory, new_promela_string)
            # execute spin verification
            # TODO: this output isn't as useful as trail file, maybe can use later if needed.
            cli_ret, err = execute_shell_cmd(
                [self.spin_path, "-search", "-a", "-O2", self.promela_path]
            )
            # if you didn't get an error from validation step, no more retries
            if cli_ret != 0:
                self.logger.error(f"Failed to execute spin command with error: {err}")
                prompt = (
                    "Failure occured in SPIN validation output. Generate a new LTL: \n"
                    + err
                )
                retry += 1
                continue
            pml_file: str = self.promela_path.split("/")[-1]
            # trail file means you failed
            if os.path.isfile(pml_file + ".trail"):
                # move trail file since the promela file gets sent to self.log_directory
                os.replace(pml_file + ".trail", self.promela_path + ".trail")
                # run trail
                cli_ret, trail_out = execute_shell_cmd(
                    [self.spin_path, "-t", self.promela_path]
                )
                if cli_ret != 0:
                    self.logger.error(
                        f"Failed to execute trail file... Unable to get trace: {trail_out}"
                    )
                prompt = (
                    "Failure occured in SPIN validation output. Generate a new LTL: \n"
                    + trail_out
                )
                self.logger.debug(
                    "Retrying after failing to pass formal validation step..."
                )
            # no trail file, success
            else:
                ret = 0
                break

            retry += 1

        return ret


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
