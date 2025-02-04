from lxml import etree
import logging
import sys
from enum import Enum

# TODO: extract this from XSD.
NS: dict = {
    "xs": "http://www.w3.org/2001/XMLSchema",
    "task": "https://robotics.ucmerced.edu/task",
}

PROCTYPE: str = """
proctype execute_task(Task t) {
    if
    :: t.action.actionType == moveToLocation ->
        // Perform the moveToLocation action
        printf("Moving to location: Latitude=%d, Longitude=%d\\n", t.action.parameter1, t.action.parameter2);
    :: t.action.actionType == takeThermalPicture ->
        // Perform the takeThermalPicture action
        printf("Taking thermal picture: count=%d\\n", t.action.parameter1);
    :: t.action.actionType == takeAmbientTemperature ->
        // Perform the takeAmbientTemperature action
        printf("Taking ambient temperature: samples=%d\\n", t.action.parameter1);
    :: t.action.actionType == takeCO2Reading ->
        // Perform the takeCO2Reading action
        printf("Taking CO2 reading: samples=%d\\n", t.action.parameter1);
    fi;
}
"""

SENSOR_FN: str = """
proctype select_{}() {{
    int i;
    select (i : {}..{});
    {} = i;
    printf("{}: %d\\n", {});
}}
"""


class PromelaCompiler:
    class Sensors(str, Enum):
        CO2 = "takeCO2Reading"
        TEMP = "takeAmbientTemperature"
        THERMALCAM = "takeThermalPicture"

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
            "takeThermalPicture": "thermal",
            "takeAmbientTemperature": "temp",
            "takeCO2Reading": "co2",
        }

    def init_xml_tree(self, xml_file: str) -> None:
        self.tree: etree._ElementTree = etree.parse(xml_file)
        self.root: etree._Element = self.tree.getroot()

    def parse_xml(self) -> str:
        promela_code: str = self.promela_template
        task_defs: list[str] = []
        execution_calls: list[str] = []
        action_type_sequence: list[str] = []
        id: int = 0

        # TODO: this entire block is assuming task types. There should be a better way to do this.
        # Iterate through each atomic task in the XML
        for atomic_task in self.root.findall(".//task:AtomicTask", NS):
            task_name = atomic_task.find("task:TaskID", NS).text
            self.logger.debug(f"Parsing task {task_name}")
            action = atomic_task.find("task:Action", NS)

            action_type = action.find("task:ActionType", NS).text
            param1 = (
                action.find("task:takeThermalPicture/task:numberOfPictures", NS).text
                if action_type == "takeThermalPicture"
                else (
                    action.find(
                        "task:takeAmbientTemperature/task:numberOfSamples", NS
                    ).text
                    if action_type == "takeAmbientTemperature"
                    else (
                        action.find("task:takeCO2Reading/task:numberOfSamples", NS).text
                        if action_type == "takeCO2Reading"
                        else (
                            action.find("task:moveToLocation/task:Latitude", NS).text
                            if action_type == "moveToLocation"
                            else "0"
                        )
                    )
                )
            )
            param2 = (
                action.find("task:moveToLocation/task:Longitude", NS).text
                if action_type == "moveToLocation"
                else "0"
            )

            # Create Promela definitions for each task
            task_def: str = f"Task {task_name};\n"
            task_defs.append(task_def)

            # TODO: we should be able to parse this information off of the template file
            # Initialize the task action
            # TODO: THIS IS BECAUSE SPIN CAN'T HANDLE FLOATS
            if action_type == "moveToLocation":
                execution_calls.append(
                    f"""
    {task_name}.id = {id};
    {task_name}.action.actionType = {action_type};
    {task_name}.action.parameter1 = {round(float(param1) * 1e5)};
    {task_name}.action.parameter2 = {round(float(param2) * 1e5)};
    run execute_task({task_name});\n"""
                )
            else:
                execution_calls.append(
                    f"""
    {task_name}.id = {id};
    {task_name}.action.actionType = {action_type};
    {task_name}.action.parameter1 = {round(float(param1))};
    {task_name}.action.parameter2 = {round(float(param2))};
    run execute_task({task_name});\n"""
                )
            id += 1
            action_type_sequence.append(action_type)

        self.task_names = "".join(task_defs)
        self._handle_conditional(execution_calls, action_type_sequence)

        # Concatenate task definitions and execution calls
        promela_code += self.task_names
        promela_code += "\n"
        promela_code += "".join(self.globals_used)
        promela_code += "".join(self.sensors_used)
        promela_code += "\ninit {\n"
        promela_code += "".join(execution_calls)
        promela_code += "\n}\n"

        promela_code += PROCTYPE

        return promela_code

    def set_promela_template(self, promela_template_path: str) -> None:
        with open(promela_template_path, "r") as file:
            self.promela_template: str = file.read()

    def get_promela_template(self) -> str:
        return self.promela_template

    def get_task_names(self) -> str:
        return self.task_names

    def _handle_conditional(
        self, execution_calls: list[str], action_type_sequence: list[str]
    ) -> None:
        run_proctype: str = "    run select_{}();\n"
        if_statement: str = "\n    if \n"
        conditional_statement: str = "    :: {} {} {} ->"
        end_if: str = "    :: else -> skip\n    fi\n"

        task_sequence = self.root.find(".//task:ActionSequence", NS).find(
            "task:Sequence", NS
        )
        # count current task, from top to bottom of the ActionSequence
        idx: int = 0
        offset: int = 0
        reset_if: bool = True
        reset_offset: int = 0
        for c in task_sequence.iterchildren():
            # if this particular child is a conditional on the previous task
            if c.tag == "{" + NS["task"] + "}" + "ConditionalActions":
                if reset_if:
                    # this adds the function call in PML system after task object is initialized
                    execution_calls.insert(
                        idx, run_proctype.format(action_type_sequence[offset - 1])
                    )
                    idx += 1
                    execution_calls.insert(idx, if_statement)
                    idx += 1
                    reset_if = False
                reset_offset += 1
                # this format should be guaranteed by the schema
                if c.find("task:Conditional/task:Comparator", NS) is not None:
                    comp = c.find("task:Conditional/task:Comparator", NS).text
                    val = c.find("task:Conditional/task:HardValue", NS).text
                    # this added conditional statement for result of sensor sample
                    execution_calls.insert(
                        idx,
                        conditional_statement.format(
                            self.actions_to_pml_global[
                                action_type_sequence[offset - reset_offset]
                            ],
                            self.xml_comp_to_promela[comp],
                            int(round(float(val))),
                        ),
                    )
                    # have to cast as float then round because of Promela only handling ints
                    self._add_sensor_proctype(
                        action_type_sequence[offset - reset_offset],
                        int(round(float(val))),
                    )
                    # add a global variable in PML to keep track of sensor readings
                    self._add_global(action_type_sequence[offset - reset_offset])
                    idx += 1
                # TODO: right now husky schema doesn't support bool operators since no sensor requires it
                #       add this in when required.
                elif c.find("task:Conditional/task:ReturnStatus", NS) is not None:
                    self.logger.warning(f"Currently not supported...: {c}")
                else:
                    self.logger.error(f"Bad comparator: {c}")

            # if your next sibling is not another comparator, close if
            if c.getnext() is not None:
                if (
                    c.tag == "{" + NS["task"] + "}" + "ConditionalActions"
                    and c.getnext().tag != "{" + NS["task"] + "}" + "ConditionalActions"
                ):
                    execution_calls.insert(idx + 1, end_if)
                    idx += 1
                    reset_if = True
                    reset_offset = 0
            # if you're at the end of the list and you have a condition, close if
            else:
                if c.tag == "{" + NS["task"] + "}" + "ConditionalActions":
                    execution_calls.insert(idx + 1, end_if)
                    idx += 1
                    reset_if = True
                    reset_offset = 0
            idx += 1
            offset += 1

    def _add_sensor_proctype(self, action_type: str, val: int) -> None:
        # check that sensor proctype hasnt been added already
        if not any(action_type in s for s in self.sensors_used):
            proctype: str = SENSOR_FN.format(
                action_type,
                val - 1,
                val + 1,
                self.actions_to_pml_global[action_type],
                self.actions_to_pml_global[action_type],
                self.actions_to_pml_global[action_type],
            )
            self.sensors_used.append(proctype)

    def _add_global(self, action_type: str) -> None:
        # check that global variable hasnt been added already
        if not any(
            self.actions_to_pml_global[action_type] in s for s in self.globals_used
        ):
            self.globals_used.append(
                "int " + self.actions_to_pml_global[action_type] + ";\n"
            )


def main():
    logger: logging.Logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.DEBUG)

    pc: PromelaCompiler = PromelaCompiler(
        "app/resources/context/wheeled_bots/promela_template.txt", logger
    )
    # this should be the path to the XML mission file
    pc.init_xml_tree(sys.argv[1])
    logger.info(pc.parse_xml())


if __name__ == "__main__":
    main()
