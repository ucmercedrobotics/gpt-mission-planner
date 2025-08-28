import logging
import sys
from enum import Enum
from typing import Tuple

from lxml import etree

from utils.xml_utils import NS


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
    ABSTRACTVALUE = "AbstractValue"
    ACTION = "Action"
    ACTIONSEQUENCE = "ActionSequence"
    ACTIONTYPE = "ActionType"
    ATOMICTASKS = "AtomicTasks"
    COMPARATOR = "Comparator"
    CONDITONAL = "Conditional"
    CONDITIONALACTIONS = "ConditionalActions"
    CONDITIONALEXPRESSION = "ConditionalExpression"
    HARDVALUE = "HardValue"
    PARAMETER = "Parameter"
    PARAMETERS = "Parameters"
    PRECONDITION = "Precondition"
    PRECONDITIONS = "Preconditions"
    RETURNSTATUS = "ReturnStatus"
    SEQUENCE = "Sequence"
    TASKID = "TaskID"
    VARIABLENAME = "VariableName"
    VARIABLEVALUE = "VariableValue"


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
        }

    def init_xml_tree(self, xml_file: str) -> None:
        self.root: etree._Element = etree.fromstring(xml_file)

    def parse_code(self) -> str:
        promela_code: str = self.promela_template
        task_defs: list[str] = []
        execution_calls: list[str] = []
        self.reset()

        task_sequence: etree._Element = self.root.find(
            ".//task:ActionSequence", NS
        ).find("task:Sequence", NS)

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
    ):
        """
        Defines behavior tree recursively using Leaf classes defined above.
            root -> next1 -> next2
             /\                /
        leaf1  leaf2        leaf5
                 /\
            leaf3  leaf4
        """
        run_proctype: str = "select ({} : {}..{});\n\n"
        if_statement: str = "if \n"
        conditional_statement: str = ":: {} {} {} -> \n"
        end_if: str = ":: else -> skip\n    fi\n\n"
        first_if: bool = True
        action_type: str = ""
        running_conditional: bool = False

        for t in sequence:
            if t.tag == "{" + NS["task"] + "}" + "TaskID":
                execution_calls.append(indent)
                action_type = self._map_task(t.text)
                # this represents the second branch of behavior as first is covered in nested conditional above ^
                task_defs.append("Task " + t.text + ";\n")
                execution_calls.append(
                    t.text + ".action.actionType = " + action_type + ";\n"
                )
                running_conditional = False
            elif t.tag == "{" + NS["task"] + "}" + "ConditionalActions":
                cond: etree._Element = t.find("task:Conditional", NS)
                g, c, v = self._parse_conditional_xml(
                    cond, action_type, running_conditional
                )
                running_conditional = True
                if first_if:
                    execution_calls.append(indent)
                    execution_calls.append(
                        run_proctype.format(g, int(v) - 1, int(v) + 1)
                    )
                    execution_calls.append(indent)
                    execution_calls.append(if_statement)
                    first_if = False
                execution_calls.append(indent)
                execution_calls.append(conditional_statement.format(g, c, v))
                # recursively iterate down this branch
                self._define_tree(
                    t.find("task:Sequence", NS),
                    task_defs,
                    execution_calls,
                    indent + "    ",
                )
                if t.getnext() is not None:
                    if t.getnext().tag != "{" + NS["task"] + "}" + "ConditionalActions":
                        execution_calls.append(indent)
                        execution_calls.append(end_if)
                        first_if = True
                else:
                    execution_calls.append(indent)
                    execution_calls.append(end_if)
                    first_if = True
            # if there is a comment in the XML, skip it
            elif isinstance(t, etree._Comment):
                continue
            else:
                self.logger.error(f"Found unknown element tag: {t.tag}")

    def _parse_conditional_xml(
        self, conditional: etree._Element, action_type: str, running_conditional: bool
    ) -> Tuple[str, str, str]:

        rs: etree._Element = conditional.find("task:" + ElementTags.RETURNSTATUS, NS)
        comp: etree._Element = conditional.find("task:" + ElementTags.COMPARATOR, NS)

        if rs is not None:
            c = "=="
            v = rs.text
        elif comp is not None:
            c = self.xml_comp_to_promela[comp.text]
            v = str(round(float(conditional.find("task:HardValue", NS).text)))
        else:
            self.logger.error(f"Invalid conditional sequence for {action_type}")
            raise Exception

        # add a global variable in PML to keep track of sensor readings
        if not running_conditional:
            g = self._add_global(action_type)
        # if you're in a running conditional, take the last used global
        else:
            g = self.globals_used[-1]

        return g, c, v

    def _map_task(self, name: str) -> str:
        """
        Finds base object in XML that contains atomic definition.
        Used before adding task to list to define what task is
            1. Find order of task
            2. Define task
            3. Add task to sequence
        """

        # TODO: use self.root to explore AtomicTasks and define TaskLeaf
        # find <AtomicTasks> from root
        atomic_tasks: etree._Element = self.root.find(
            "task:" + ElementTags.ATOMICTASKS, NS
        )
        # find the <AtomicTask> in the list that matches the current task
        task: etree._Element = self._find_child(
            atomic_tasks, "task:" + ElementTags.TASKID, name
        )

        # <Action>
        #   <Action>
        #       <ActionType>we want this string</ActionType>
        #       ...
        action_type: etree._Element = task.find("task:" + ElementTags.ACTION, NS).find(
            "task:" + ElementTags.ACTIONTYPE, NS
        )

        return action_type.text

    def _add_global(self, action_type: str) -> str:
        sensor_var: str = self.actions_to_pml_global[action_type] + str(
            len(self.globals_used)
        )
        self.globals_used.append(sensor_var)
        return sensor_var

    @staticmethod
    def _find_child(parent: etree._Element, tag_name: str, text: str):
        """
        Helper function to find a child based on interior text:
        parent : current Element
        tag_name: tag name you're searching for text in
        text: text you're searching for

        <root>
            <tag_name>text</tag_name>
        </root>
        """
        for c in parent:
            if c is None:
                continue
            task_id = c.find(tag_name, NS)
            if task_id is None:
                continue
            # if you have a match
            if text == task_id.text:
                return c


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
