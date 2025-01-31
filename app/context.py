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
            "content": 'You are a mission planner that generates navigational XML mission plans based on robotic task representation. \
                        When asked to generate a mission, create an XML file conformant to the known schema and \
                        use the GeoJSON file to provide references in the mission plan for things such as GPS location, tree type, etc. \
                        In order to accomplish most actions, you must first drive to the location. \
                        Therefore, tasks should almost always require driving to a tree and then doing an action unless doing multiple actions at the same tree. \
                        Place the original question in the TaskDescription element of the CompositeTaskInformation element for logging. \
                        Please format your answers using XML markdown as such: ```xml answer ```. ',
        },
        # context
        {
            "role": "user",
            "content": "Here is the schema that represent that available robot you have to accomplish your mission. \
                    It is critical that the XML validates against the schema and that the schema location attribute is included in the root tag. \
                    Please include the XSI schema location every time you generate a mission with it's namespace in the attribute. \
                    For example, `xsi:schemaLocation=\"https://robotics.ucmerced.edu/task ./app/resources/context/wheeled_bots/schema.xsd\">` \
                    The mission must be syntactically correct and validate using an XML linter: "
            + schema,
        },
    ]

    return context


def verification_agent_context(schema: str) -> list:
    context: list = [
        {
            "role": "user",
            "content": 'You are a linear temporal logic generator that generates Spin compatible LTL missions based on mission input. \
                            You should generate a single LTL that has the following 3 properties: \
                            A safety property (something bad never happens) \
                            A liveness property (something good eventually happens) \
                            A fairness condition. \
                            Please ensure that this LTL conforms to Spin syntax and can be compiled. \
                            Also, please format your answer with markdown for LTL as such: ```ltl answer here ```',
        },
        {   "role": "user",
            "content": "Here is an example of what the LTL should look like. \
            Reminder, the LTL should be as verbose and capture as many of the states as it possibly can to ensure full validation. \
            Note also that you should try your best to not join assertions together. Meaning, each of the 3 properties should have multiple assertions under and not one large one.: \
            ```ltl \
            ltl mission { \
                /* Safety property */ \
                []!(Task002.action.actionType == takeThermalPicture && Task001.action.actionType != moveToLocation) &&\
                []!(Task004.action.actionType == takeThermalPicture && Task003.action.actionType != moveToLocation) &&\
                []!(Task006.action.actionType == takeThermalPicture && Task005.action.actionType != moveToLocation) &&\
                /* Liveness property */\
                <> (Task002.action.actionType == takeThermalPicture) &&\
                <> (Task004.action.actionType == takeThermalPicture) &&\
                <> (Task006.action.actionType == takeThermalPicture) &&\
                /* Fairness property */\
                [](Task001.action.actionType == moveToLocation -> <> (Task002.action.actionType == takeThermalPicture)) &&\
                [](Task003.action.actionType == moveToLocation -> <> (Task004.action.actionType == takeThermalPicture)) &&\
                [](Task005.action.actionType == moveToLocation -> <> (Task006.action.actionType == takeThermalPicture))\
            }\
            ``` "},
        # context
        {
            "role": "user",
            "content": "Note that this is the XSD file that all missions will be generated by. \
                            When generating LTL equations, please map the types in this file to the types in the Promela template file."
            + schema,
        },
    ]

    return context
