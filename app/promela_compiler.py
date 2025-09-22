import logging
import sys
from enum import Enum
from typing import Tuple

from lxml import etree


SENSOR_FN: str = """
proctype select_{}() {{
    d_step {{
        int i;
        select (i : {}..{});
        {} = i;
        printf("{}: %d\\n", {});
    }}
}}
"""


class ElementTags(str, Enum):
    MoveToGPSLocation = "MoveToGPSLocation"
    DetectObject = "DetectObject"
    BoolCondition = "BoolCondition"
    Sequence = "Sequence"
    Fallback = "Fallback"
    Parallel = "Parallel"


class PromelaCompiler:
    def __init__(self, promela_template: str, logger: logging.Logger):
        # TODO: abstract these hardcodes away to something that parses the XSD
        self.set_promela_template(promela_template)
        self.logger: logging.Logger = logger
        # this is to be given to LLM to match syntax with object names in PML
        self.task_names: str = ""
        # keeping track of specific sensors used from list
        self.sensors_used: list[str] = []
        self.globals_used: list[str] = []
        self.xml_comp_to_promela: dict = {
            "lt": "<",
            "lte": "<=",
            "gt": ">",
            "gte": ">=",
            "eq": "==",
            "neq": "!=",
        }
        self.actions_to_pml_global: dict = {
            "takeThermalPicture": "thermalSample",
            "takeAmbientTemperature": "temperatureSample",
            "takeCO2Reading": "co2Sample",
            "detectObject": "detectionStatus",
        }

    def init_xml_tree(self, xml_file: str) -> None:
        self.root: etree._Element = etree.fromstring(xml_file)

    def parse_code(self) -> str:
        promela_code: str = self.promela_template
        task_defs: list[str] = []
        execution_calls: list[str] = []
        self.reset()

        task_sequence: etree._Element = self.root.find("BehaviorTree").find("Sequence")

        self._define_tree(task_sequence, task_defs, execution_calls)

        self.task_names = "".join(task_defs)
        global_list: list[str] = [f"int {x};\n" for x in self.globals_used]

        # Concatenate task definitions and execution calls
        promela_code += "\n"
        promela_code += self.task_names
        promela_code += "\n"
        promela_code += "".join(global_list)
        promela_code += "\ninit {\n    atomic {\n"
        promela_code += "".join(execution_calls)
        promela_code += "\n    }\n}"

        return promela_code

    def set_promela_template(self, promela_template_path: str) -> None:
        with open(promela_template_path, "r") as file:
            self.promela_template: str = file.read()

    def get_promela_template(self) -> str:
        return self.promela_template

    def get_task_names(self) -> str:
        return self.task_names

    def get_globals(self) -> str:
        return "".join([f"int {g};\n" for g in self.globals_used])

    def reset(self) -> None:
        self.task_names = ""
        # keeping track of specific sensors used from list
        self.sensors_used = []
        self.globals_used = []

    def _define_tree(
        self,
        sequence: etree._Element,
        task_defs: list[str],
        execution_calls: list[str],
        indent: str = "    ",
        conditional: bool = False,
    ):
        """
        Defines behavior tree recursively using Leaf classes defined above.
            root -> next1 -> next2
             /\                /
        leaf1  leaf2        leaf5
                 /\
            leaf3  leaf4
        """
        # run_proctype: str = "select ({} : {}..{});\n\n"
        # if_statement: str = "if \n"
        # conditional_statement: str = ":: {} {} {} -> \n"
        # end_if: str = ":: else -> skip\n    fi\n\n"
        # action_type: str = ""
        first_condition: bool = True

        for t in sequence:
            if t.tag in ElementTags.__dict__.values():
                if t.tag == ElementTags.Sequence:
                    # recurse
                    self._define_tree(
                        t, task_defs, execution_calls, indent, conditional
                    )
                elif t.tag == ElementTags.Fallback:
                    self._define_tree(
                        t, task_defs, execution_calls, indent + "    ", True
                    )
                    # self._evaluate_condition(t[0], task_defs, execution_calls, indent + "    ")
                    execution_calls.append("    " + "fi\n")
                elif t.tag == ElementTags.Parallel:
                    pass  # TODO
                else:
                    if t.get("name") is not None:
                        task_defs.append("Task " + t.get("name") + ";\n")
                        if conditional and not first_condition:
                            execution_calls.append("    " + ":: else ->\n")
                        execution_calls.append(
                            indent
                            + t.get("name")
                            + ".action.actionType = "
                            + t.tag
                            + ";\n"
                        )
                    # we assume its a Condition
                    else:
                        if t.tag == ElementTags.BoolCondition:
                            val: str = t.get("value")
                            expected: str = t.get("expected")
                            execution_calls.append(
                                "    "
                                + "select ({} : {}..{});\n\n".format(
                                    val[1:-1], "0", "1"
                                )
                            )
                            execution_calls.append("    " + "if\n")
                            execution_calls.append("    " + ":: ")
                            if val is not None and expected is not None:
                                execution_calls.append(
                                    f"{val[1:-1]} == {1 if expected else 0} ->\n"
                                )
                                self._add_global(val[1:-1])
                            continue
                first_condition = False
            else:
                self.logger.error(f"Found unknown element tag: {t.tag}")

    def _add_global(self, action_type: str) -> str:
        self.globals_used.append(action_type)
        return action_type


def main():
    logger: logging.Logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.DEBUG)

    pc: PromelaCompiler = PromelaCompiler(
        "app/resources/context/formal_verification/promela_template.txt", logger
    )
    # this should be the path to the XML mission file
    with open(sys.argv[1]) as fp:
        xml: str = fp.read()

    pc.init_xml_tree(xml)
    logger.info(pc.parse_code())


if __name__ == "__main__":
    main()
