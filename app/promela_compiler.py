from lxml import etree
import logging

# TODO: extract this from XSD.
NS: dict = {
        'xs': 'http://www.w3.org/2001/XMLSchema',
        'task': 'https://robotics.ucmerced.edu/task'
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

class PromelaCompiler():
    def __init__(self, promela_object_header: str, logger: logging.Logger):
        # TODO: abstract these hardcodes away to something that parses the XSD 
        self.promela_object_header: str = promela_object_header
        self.logger: logging.Logger = logger

    def init_xml_tree(self, xml_file: str) -> None:
        self.tree: etree._ElementTree = etree.parse(xml_file)
        self.root: etree._Element = self.tree.getroot()

    def parse_xml(self) -> str:
        promela_code: str = self.promela_object_header
        task_defs: list[str] = []
        execution_calls: list[str] = []
        id: int = 0

        # TODO: this entire block is assuming task types. There should be a better way to do this.
        # Iterate through each atomic task in the XML
        for atomic_task in self.root.findall('.//task:AtomicTask', NS):
            # NOTE: we make a hard assumption here we can constrain GPT to answer with format "Task#"
            task_id = atomic_task.find('task:TaskID', NS).text
            action = atomic_task.find('task:Action', NS)
            
            action_type = action.find('task:ActionType', NS).text
            param1 = action.find('task:takeThermalPicture/task:numberOfPictures', NS).text if action_type == "takeThermalPicture" else \
                    action.find('task:takeAmbientTemperature/task:numberOfSamples', NS).text if action_type == "takeAmbientTemperature" else \
                    action.find('task:takeCO2Reading/task:numberOfSamples', NS).text if action_type == "takeCO2Reading" else \
                    action.find('task:moveToLocation/task:Latitude', NS).text if action_type == "moveToLocation" else "0"
            param2 = action.find('task:moveToLocation/task:Longitude', NS).text if action_type == "moveToLocation" else "0"

            # Create Promela definitions for each task
            task_def: str = f"Task {task_id};\n"
            task_defs.append(task_def)

            # TODO: we should be able to parse this information off of the template file
            # Initialize the task action
            # TODO: THIS IS BECAUSE SPIN CAN'T HANDLE FLOATS
            if action_type == "moveToLocation":
                execution_calls.append(f"""
    {task_id}.id = {id};
    {task_id}.action.actionType = {action_type};
    {task_id}.action.parameter1 = {round(float(param1) * 1e5)};
    {task_id}.action.parameter2 = {round(float(param2) * 1e5)};
    run execute_task({task_id});
        """)
            else:
                execution_calls.append(f"""
    {task_id}.id = {id};
    {task_id}.action.actionType = {action_type};
    {task_id}.action.parameter1 = {round(float(param1))};
    {task_id}.action.parameter2 = {round(float(param2))};
    run execute_task({task_id});
        """) 
            id += 1

        # Concatenate task definitions and execution calls
        promela_code += "".join(task_defs)
        promela_code += "\ninit {\n"
        promela_code += "".join(execution_calls)
        promela_code += "\n}\n"

        promela_code += PROCTYPE

        return promela_code