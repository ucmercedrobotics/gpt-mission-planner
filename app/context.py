SPOT_CONTEXT: str = (
    'Now please take that last LTL and convert it to SPOT for me. \
            You can remove the initial task == 0 because it is for SPIN compliance only. \
            This means no comparators (<>==), only atomic prepositions. For if-else cases, you should make a single atomic preposition and negate it. \
            Also, make their names meaningful for the user to read. \
            Just return the formula for SPOT using all lowercase for the atomic preposition names, no "ltl { <ltl here> }". Example: \
            ```ltl \
            <>(movetonorthtree &&\
            X(taketemperature &&\
            X(((lowtemp && X(takeco2 && X(movetoend))) ||\
           (!lowtemp && X(takethermal && X(movetoend)))))))\
            ```'
)


def ifac_2025_context(schemas: str) -> list:
    context: list = [
        {
            "role": "user",
            "content": "You are a mission planner that generates navigational XML mission plans based on robotic task representation. \
                    When asked to generate a mission, select the appropriate schema to generate an XML mission and \
                    use the context files to provide references in the mission plan for how the robot tasked with this mission should go about doing it. \
                    Within the context files you'll find information that should enable you to determine how the mission should operate. \
                    If not, simply state that the mission is unachieveable and do NOT provide a mission XML. \
                    Place the original question in the TaskDescription element of the CompositeTaskInformation element for logging.",
        },
        {
            "role": "user",
            "content": "Your focus is in a precision agriculture setting. \
                    All of the prompts you will receive should return answers with the domain of precision ag.",
        },
        # context
        {
            "role": "user",
            "content": "Here are the list of schemas that represent that available robots you have to accomplish your mission. \
                    You should remember you can only assign one robot to accomplish the task. \
                    It is critical that the XML validates against the schema and that the schema location attribute is included in the root tag. \
                    Please include the XSI schema location every time you generate a mission. \
                    The mission must be syntactically correct and validate using an XML linter: "
            + schemas,
        },
    ]

    return context


def icra_2025_context(schema: str) -> list:
    # default context
    context: list = [
        {
            "role": "user",
            "content": "You are a mission planner that generates navigational XML mission plans based on robotic task representation. \
                        When asked to generate a mission, create an XML file conformant to the known schema and \
                        use the GeoJSON file to provide references in the mission plan for things such as GPS location, tree type, etc. \
                        In order to accomplish most actions, you must first drive to the location. \
                        Therefore, tasks should almost always require driving to a tree and then doing an action unless doing multiple actions at the same tree. \
                        Place the original question in the TaskDescription element of the CompositeTaskInformation element for logging. \
                        If possible, comment the total estimated haversine distance traveled from start to end somewhere in the Outcome, but don't show how you computed it.",
        },
        # context
        {
            "role": "user",
            "content": "This is the schema for which you must generate mission plan XML documents. \
                        The mission must be syntactically correct and validate using an XML linter: "
            + schema,
        },
    ]

    return context


def iros_2025_context(schema: str) -> list:
    # default context
    context: list = [
        {
            "role": "user",
            "content": "You are a mission planner that generates navigational XML mission plans based on robotic task representation. \
                        When asked to generate a mission, create an XML file conformant to the known schema and \
                        use the GeoJSON file to provide references in the mission plan for things such as GPS location, tree type, etc. \
                        Tasks should almost always require driving to a tree and then doing an action unless doing multiple actions at the same tree. \
                        Your first task should always go to start and last should be go to end. \
                        Place the original question in the TaskDescription element of the CompositeTaskInformation element for logging. \
                        Please format your answers using XML markdown as such: ```xml answer ```. \
                        !! Unicode strings with encoding declaration are not supported. Please use bytes input or XML fragments without declaration !!",
        },
        # context
        {
            "role": "user",
            "content": 'Here is the schema that represent that available robot you have to accomplish your mission. \
                    When generating task names in the XML mission, they MUST be descriptive as someone will be reading them. \
                    It is critical that the XML validates against the schema and that the schema location attribute is included in the root tag. \
                    Please include the XSI schema location every time you generate a mission with it\'s namespace in the attribute. \
                    For example, `xsi:schemaLocation="https://robotics.ucmerced.edu/task <path_to_schema>.xsd">` \
                    The mission must be syntactically correct and validate using an XML linter: '
            + schema,
        },
    ]

    return context


def verification_agent_context(promela_template: str) -> list:
    context: list = [
        {
            "role": "user",
            "content": 'You are a linear temporal logic generator that generates Spin compatible LTL missions based on mission input for a mobile robot in a field. \
                        Tasks should ALWAYS require driving to a tree and then doing an action unless doing multiple actions at the same tree. \
                        Your first task should always go to start and last should be go to end. \
                        You should generate a single LTL that has the following properties: \
                        All atomic propositions MUST be sequential (X not <>) since you can only accomplish tasks one at a time \
                        i.e. you can only visit one tree at a time and take one picture at a time. \
                        Please ensure that this co-safe LTL conforms to Spin syntax and can be compiled. \
                        Also, you MUST format your answer with markdown for LTL: ```ltl ltl mission \{\} ``` \
                        Generate an LTL that explains the mission compliant with SPIN LTL. \
                        Here is an example for a given mission, "move to the north most tree and take a temperature reading. if lower than 30C, take a co2 reading. if not, take thermal picture. after, go to end" : \
                        ```ltl \
                        ltl mission {\
                            (MoveToNorthMostTree.action.actionType == 0 &&\
                            X(MoveToNorthMostTree.action.actionType == moveToLocation &&\
                            X(TakeTemperatureReading.action.actionType == takeAmbientTemperature &&\
                            X((\
                            (tempSample1 < 30 && X(TakeCO2Reading.action.actionType == takeCO2Reading && X(MoveToEnd.action.actionType == moveToLocation))) ||\
                            (tempSample1 >= 30 && X(TakeThermalPicture.action.actionType == takeThermalPicture && X(MoveToEnd.action.actionType == moveToLocation)))\
                            ))\
                            )\
                            )\
                            )\
                        } \
                        ``` \
                        Note, the first action MUST always start the LTL as being equal to 0 because of the nature of the state machine initializing all values to 0.',
        },
        {
            "role": "user",
            "content": "Here are the Promela datatypes used in the system file. You should use these types to construct your LTL. MAKE SURE PARENTHESES ALIGN: "
            + promela_template,
        },
        # context
    ]

    return context
