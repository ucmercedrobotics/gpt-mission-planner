import logging
import sys

from lxml import etree

from xml_types import ControlTags, ActionTags, ConditionalTags

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
        fallback: bool = False,
    ):
        else_statement: str = ":: else ->"
        sequence_count: int = len(sequence.findall("Sequence"))

        for t in sequence:
            if t.tag == ControlTags.Sequence:
                # recurse
                self._define_tree(t, task_defs, execution_calls, indent)
                sequence_count -= 1
                if fallback:
                    if sequence_count > 0:
                        execution_calls.append(indent[:-4] + else_statement + "\n")
                    else:
                        execution_calls.append(indent[:-4] + else_statement + " skip\n")
                    fallback = False
            elif t.tag == ControlTags.Fallback:
                execution_calls.append(indent + "if\n")
                execution_calls.append(indent + ":: ")
                self._define_tree(t, task_defs, execution_calls, indent + "    ", True)
                execution_calls.append(indent + "fi\n")
            elif t.tag == ControlTags.Parallel:
                pass  # TODO
            elif t.tag in ActionTags.__dict__.values():
                if t.get("name") is not None:
                    task_defs.append("Task " + t.get("name") + ";\n")
                    execution_calls.append(
                        indent + t.get("name") + ".action.actionType = " + t.tag + ";\n"
                    )
                # we assume its a Condition
            elif t.tag in ConditionalTags.__dict__.values():
                if t.tag == ConditionalTags.AssertTrue:
                    result: str = t.get("result")
                    if result is not None:
                        execution_calls.insert(
                            -2,
                            (
                                indent[:-4]
                                + "select ({} : {}..{});\n\n".format(
                                    result[1:-1], "0", "1"
                                )
                            ),
                        )
                        execution_calls.append(f"{result[1:-1]} == 1 ->\n")
                        self._add_global(result[1:-1])
                    continue
                elif t.tag == ConditionalTags.CheckValue:
                    val: str = t.get("value")
                    threshold: str = t.get("threshold")
                    comp: str = t.get("comp")
                    execution_calls.insert(
                        -2,
                        (
                            indent[:-4]
                            + "select ({} : {}..{});\n\n".format(
                                val[1:-1],
                                str(int(threshold) - 1),
                                str(int(threshold) + 1),
                            )
                        ),
                    )
                    if val is not None and threshold is not None and comp is not None:
                        execution_calls.append(
                            f"{val[1:-1]} {self.xml_comp_to_promela[comp]} {threshold} ->\n"
                        )
                        self._add_global(val[1:-1])
                    continue
            else:
                self.logger.warning(f"Unknown tag in XML: {t.tag}")

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
