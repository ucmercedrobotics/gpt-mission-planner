import logging
import os
from typing import Tuple, Any

from gpt_interface import LLMInterface
from utils.os_utils import (
    execute_shell_cmd,
    write_out_file,
)
from utils.xml_utils import (
    parse_schema_location,
    parse_code,
    validate_output,
    count_xml_tasks,
)
from utils.gps_utils import TreePlacementGenerator

OPENAI: str = "openai/gpt-5"
ANTHROPIC: str = "claude-sonnet-4-20250514"
GEMINI: str = "gemini/gemini-2.5-pro"
HUMAN_REVIEW: bool = False
EXAMPLE_RUNS: int = 5


class MissionPlanner:
    def __init__(
        self,
        token_path: str,
        schema_paths: list[str],
        context_files: list[str],
        tpg: TreePlacementGenerator,
        max_retries: int,
        max_tokens: int,
        temperature: float,
        ltl: bool,
        promela_template_path: str,
        spin_path: str,
        log_directory: str,
        logger: logging.Logger,
    ):
        # logger instance
        self.logger: logging.Logger = logger
        # set schema and farm file paths
        self.schema_paths: list[str] = schema_paths
        self.context_files: list[str] = context_files
        # tree placement generator init
        if tpg is not None:
            self.tpg: TreePlacementGenerator = tpg
            self.tree_points: list[dict[str, Any]] = self.tpg.generate_tree_points()
        else:
            self.logger.warning(
                "No tree placement generator found. Assuming non-orchard environment..."
            )
            self.tpg = None
            self.tree_points = []
        # logging GPT output folder, make if not there
        self.log_directory: str = log_directory
        os.makedirs(self.log_directory, mode=0o777, exist_ok=True)
        # keeping track of validation status
        self.xml_valid: bool = False
        self.ltl_valid: bool = False
        # max number of times that GPT can try and fix the mission plan
        self.max_retries: int = max_retries
        # retry count, managed globally to track all failures
        self.retry: int = -1
        # init gpt interface
        self.gpt: LLMInterface = LLMInterface(
            self.logger, token_path, OPENAI, max_tokens, temperature=temperature
        )
        self.gpt.init_context(self.schema_paths, self.context_files)
        # init Promela compiler
        self.ltl: bool = ltl
        if self.ltl:
            from promela_compiler import PromelaCompiler

            self.aut: Any = None
            self.human_review: bool = HUMAN_REVIEW
            # init XML mission gpt interface
            self.pml_gpt: LLMInterface = LLMInterface(
                self.logger, token_path, OPENAI, max_tokens, temperature=temperature
            )
            # Claude human verification substitute
            self.verification_checker: LLMInterface = LLMInterface(
                self.logger, token_path, OPENAI, max_tokens, temperature=temperature
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
            import spot

            spot.setup()

    def get_promela_output_path(self) -> str:
        return self.promela_path

    def reset(self) -> None:
        self.retry = 0
        self.xml_valid = False
        self.ltl_valid = False

    # TODO stream response
    def run(self, prompt: str) -> None:
        ret: bool = False
        file_xml_out = None
        self.reset()
        # ask user for their mission plan
        xml_input: str = prompt
        ltl_input: str = prompt
        while not ret and self.retry < self.max_retries:
            # first ask of XML and LTL
            if not self.xml_valid:
                try:
                    ret, xml_out, xml_task_count = self._generate_xml(
                        xml_input, self.ltl, False
                    )
                except Exception as e:
                    self.logger.debug(f"Error generating XML: {e}")
                    ret = False
                    xml_input = str(e)
                    self.retry += 1
                    continue
                if not ret:
                    self.logger.debug(f"XML generation failed: {xml_out}")
                    xml_input = xml_out
                    continue
                # store file for logs
                file_xml_out = write_out_file(self.log_directory, xml_out)
                self.logger.debug(f"Wrote out temp XML file: {file_xml_out}")
                self.xml_valid = True
            if not self.ltl_valid and self.ltl:
                try:
                    macros, ltl_out, ltl_task_count = self._generate_ltl(ltl_input)
                except Exception as e:
                    self.logger.debug(str(e))
                    ret = False
                    ltl_input = str(e)
                    self.retry += 1
                    continue
                self.ltl_valid = True

            # if we're formally verifying
            if self.ltl:
                from .utils.spot_utils import rename_ltl_macros
                # preliminary check, but can be improved to be more thorough
                if ltl_task_count != xml_task_count:
                    more_less: str = (
                        "more" if ltl_task_count > xml_task_count else "less"
                    )
                    reconsider: str = (
                        f"You have generated {abs(ltl_task_count - xml_task_count)} {more_less} tasks in your mission than your planning agent counterpart. \
                            Please reconsider how the mission can be interpreted and reformulate, if possible."
                    )
                    # NOTE: for now we'll assume XML is correct and LTL is wrong
                    # xml_input = reconsider
                    self.xml_valid = True
                    ltl_input = reconsider
                    self.ltl_valid = False
                    self.retry += 1
                    self.logger.warning(
                        f"Task count mismatch: {xml_task_count} != {ltl_task_count}"
                    )
                    ret = False
                    continue

                self.logger.info("Generating Promela from mission...")
                # from the mission output, create an XML tree
                self.promela.init_xml_tree(xml_out)
                # generate promela string that defines mission/system
                promela_string: str = self.promela.parse_code()
                # rename variables in LTL macros to match those used in XML/tasks
                macros = rename_ltl_macros(
                    self.promela.get_task_names(), self.promela.get_globals(), macros
                )

                # checking syntax of LTL since promela is manually created
                ret, err = self._formal_verification(promela_string, macros, ltl_out)
                if not ret:
                    self.retry += 1
                    self.pml_gpt.add_context(err)
                    continue
                # does Arbiter LLM or the human agree?
                ret, err = self._spot_verification(prompt, macros)
                if not ret:
                    self.retry += 1
                    self.pml_gpt.add_context(
                        "A third party disagrees this is valid because: " + err
                    )
                    self.ltl_valid = False
                    continue
                self.ltl_valid = True
                # did you generate a trail file?
                ret, err = self._evaluate_spin_trail()
                if not ret:
                    xml_input = err
                    self.retry += 1
                    # we assume that if claude or human passed the ltl, it's the XML
                    self.xml_valid = False
                    continue

            if self.tpg is not None:
                file_xml_out = self.tpg.replace_tree_ids_with_gps(file_xml_out)
                self.logger.debug(f"Replaced tree IDs with GPS coordinates...")
                if False:
                    ret, err = self._lint_xml(open(file_xml_out, "r").read())
                    if not ret:
                        self.logger.error(
                            f"Failed to lint XML after replacing tree IDs: {err}"
                        )
                        continue

                    if not ret:
                        self.logger.error("Unable to formally verify from your prompt...")

        # clear before new query
        self.gpt.reset_context(self.gpt.initial_context_length)
        if self.ltl:
            self.pml_gpt.reset_context(self.pml_gpt.initial_context_length)

        return file_xml_out

    def _generate_xml(self, prompt: str, count: bool = False, validate: bool = True) -> Tuple[bool, str, int]:
        task_count: int = 0
        # generate XML mission
        xml_out: str | None = self.gpt.ask_gpt(prompt, True)
        self.logger.debug(xml_out)
        xml: str = parse_code(xml_out)
        # validate XML output
        if validate:
            ret, e = self._lint_xml(xml)
        else:
            return True, xml, task_count
        # check if we have a valid XML
        if not ret:
            xml = e
            self.logger.warning(f"Failure to lint XML: {e}")
        else:
            self.logger.debug(f"Successfully linted XML...")
            if count:
                self.logger.debug(f"Counting XML tasks...")
                task_count = count_xml_tasks(xml)

        return ret, xml, task_count

    def _generate_ltl(self, prompt: str) -> Tuple[str, str, int]:
        from utils.spot_utils import count_ltl_tasks

        task_count: int = 0
        # use second GPT agent to generate LTL
        ltl_out: str | None = self.pml_gpt.ask_gpt(prompt, True)
        macros: str = parse_code(ltl_out, "promela")
        # parse out LTL statement
        ltl: str = parse_code(ltl_out, "ltl")
        self.logger.debug(f"Generated Promela macros: {macros}")

        self.logger.debug(f"Generated LTL: {ltl}")

        # ask SPOT/Claude to generate automata for arbiter
        self.aut = self._convert_to_spot(ltl)
        task_count = count_ltl_tasks(self.aut)

        return macros, ltl, task_count

    def _convert_to_spot(self, ltl: str) -> Any:
        import spot
        from utils.spot_utils import regex_spin_to_spot

        """Custom Spot helper function for decoding LTL with error handling"""
        spot_out: str = regex_spin_to_spot(ltl)

        aut = spot.translate(spot_out)

        return aut

    def _lint_xml(self, xml_out: str) -> Tuple[bool, str]:
        # path to selected schema based on xsi:schemaLocation
        selected_schema: str = parse_schema_location(xml_out)
        self.logger.debug(f"Schema selected by GPT: {selected_schema}")
        # validate mission based on XSD
        ret, e = validate_output(selected_schema, xml_out)

        # check if we have a valid XML
        if ret:
            self.logger.info("Successful XML mission plan generation...")
        else:
            self.logger.error(
                f"Unable to generate mission plan from your prompt... error: {e}"
            )
            e = "Error received while validating against schema: " + e

        return ret, e

    def _formal_verification(
        self, promela_string: str, macros: str, ltl_out: str
    ) -> Tuple[bool, str]:
        ret: bool = False

        self.logger.info("Performing formal verification of LTL mission plan...")
        # generates the LTL and verifies it with SPIN; retry enabled
        ret, e = self._ltl_validation(promela_string, macros, ltl_out)
        if ret:
            self.logger.info("Successful LTL mission plan generation...")
            self.logger.debug(f"Promela description in file {self.promela_path}.")
        else:
            self.logger.error(
                "Failed to validate mission... Please see Promela error above."
            )

        return ret, e

    def _ltl_validation(
        self, promela_string: str, macros: str, ltl_out: str
    ) -> Tuple[bool, str]:
        from .utils.spot_utils import init_state_macro, add_init_state
        ret: bool = False

        macros = init_state_macro(macros)
        ltl_out = add_init_state(ltl_out)
        self.logger.debug(f"Promela macros: {macros}")
        self.logger.debug(f"Promela LTL: {ltl_out}")

        # append to promela file
        new_promela_string: str = promela_string + "\n" + macros + "\n" + ltl_out
        # write pml system and LTL to file
        self.promela_path = write_out_file(self.log_directory, new_promela_string)
        # execute spin verification
        cli_ret, e = execute_shell_cmd(
            [self.spin_path, "-search", "-a", "-O2", self.promela_path]
        )
        # if you didn't get an error from validation step, no more retries
        if cli_ret != 0:
            self.logger.error(f"Failed to execute spin command with syntax error: {e}")
        else:
            ret = True

        return ret, e

    def _spot_verification(self, mission_query: str, macros: str) -> Tuple[bool, str]:
        from utils.spot_utils import generate_accepting_run_string

        ret: bool = False
        e: str | None = ""

        runs: list[str] = [
            generate_accepting_run_string(self.aut) for _ in range(EXAMPLE_RUNS)
        ]
        runs_str: str = "\n".join(runs)

        if self.human_review:
            resp: str = ""
            while resp != ("y" or "n"):
                resp = input(
                    "Here are 5 example executions of your mission: "
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
                In your opinion, would you say that ALL of these examples are faithful to requested mission? \
                Don\'t concern yourself with low-level details (such as task parameters), just make sure action sequence is correct. \
                Mission request: \n'
                + mission_query
                + "\nContext:\n"
                + "".join([open(f).read() for f in self.context_files])
                + "\nAtomic Proposition definitions:\n"
                + macros
                + "\nExample runs:\n"
                + runs_str
            )
            self.logger.debug(f"Asking Arbiter: {ask}")
            acceptance = self.verification_checker.ask_gpt(ask, True)
            assert isinstance(acceptance, str)

            self.logger.debug(f"Arbiter says {acceptance}")

            if "yes" in acceptance.lower():
                self.logger.info("Arbiter approves. Mission proceeding...")
                ret = True
            else:
                self.logger.warning(f"Arbiter disapproves. See example runs: {runs}")
                e = self.verification_checker.ask_gpt(
                    "Can you explain why you disagree?"
                )
                self.logger.debug(str(e))

            self.verification_checker.reset_context(
                self.verification_checker.initial_context_length
            )

        self.aut.save("spot.aut", append=False)

        assert isinstance(e, str)

        return ret, e

    def _evaluate_spin_trail(self) -> Tuple[bool, str]:
        pml_file: str = self.promela_path.split("/")[-1]
        e: str = ""
        ret: bool = True

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
            e = (
                "We converted this XML mission to Promela and ran it through SPIN. Failure occured in SPIN validation output. Generate a new XML mission: \n"
                + trail_out
            )
            self.logger.debug(
                "Retrying after failing to pass formal validation step..."
            )
            ret = False

        return ret, e
