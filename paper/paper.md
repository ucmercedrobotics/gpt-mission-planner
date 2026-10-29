---
title: 'GPTMissionPlanner: A containerized, LLM-based mission planner for ROS 2 robots using BehaviorTree.CPP'
tags:
  - Python
  - ROS 2
  - behavior trees
  - large language models
  - mission planning
  - agricultural robotics
  - Docker
authors:
  - name: Marcos Abel Zuzuárregui
    orcid: 0009-0000-2667-815X
    corresponding: true
    affiliation: 1
  - name: Stefano Carpin
    orcid: 0000-0003-3837-7463
    affiliation: 1
affiliations:
  - name: Department of Computer Science and Engineering, University of California, Merced, United States
    index: 1
    ror: 00d9ah105
date: 29 October 2026  # TODO: set to the submission date
bibliography: paper.bib
---

# Summary

GPTMissionPlanner converts typed or spoken natural language (NL) requests into BehaviorTree.CPP (BT.CPP) [@faconti_behavior_tree_cpp_2018] missions for ROS 2 [@macenski_robot_2022] robots.
A large language model (LLM) decomposes each request into the action primitives of the target robots.
An XML schema check rejects plans that a robot cannot execute, and returns the error to the LLM so that it can correct the plan.
Optional formal verification checks plan intent with temporal logic.
Robots register with the planner, which divides each mission across the available fleet.
The planner ships as one container and runs with cloud or local models.
We have demonstrated it in commercial pistachio and citrus orchards with a mobile robot and a mounted manipulator [@zuzuarregui2026tfr].

# Statement of need

As robotics expands into new domains, the demand for simple system deployment grows with it.
In precision agriculture, our domain of interest, robots must operate in partially structured environments, often with limited network connectivity, and perform tasks that require reliability and safety under uncertain conditions [@AccessSurvey; @wang2024survey; @droukas2023survey].
They will also be operated mostly by non-technical users.
Adoption in such settings depends not only on performance, but also on the accessibility, maintainability, and adaptability of the software.
A robot that cannot be easily configured, understood, or repurposed will likely remain confined to research use, regardless of its technical capabilities.

GPTMissionPlanner links NL mission specification to robot execution through BT.CPP, the behavior tree (BT) engine of the ROS 2 navigation stack Nav2.
A ROS 2 robot needs only a schema of its action primitives and BT.CPP nodes that call its existing action servers; it does not need to change its software stack.
The software has three groups of users.
Operators, such as farmers, give missions in their own words through a browser, without learning a robot interface.
Integrators connect a new or existing ROS 2 robot by writing one schema and a set of BT.CPP nodes.
Researchers use the planner as a base for work on LLM-based planning.

# State of the field

LLMs are now a common front end for robot autonomy [@ahn_as_2022; @liang_code_2023].
Early systems translated user intent into executable scripts [@liang_code_2023; @huang_language_2022], but often lacked verifiable outputs or broke in unstructured environments.
Planners built on the Planning Domain Definition Language (PDDL) need a complete model of the world [@zeng_emboteam_2026] and an operator familiar with the syntax.
More recent work grounds the LLM with external validators, through syntax feedback loops [@CarpinLLMSubmitted] or formal verification [@zuzuarregui2025asyouwish], or by generating BTs that link high-level intent to low-level primitives [@ao_llm-as-bt-planner_2025; @11127942; @izzo_btgenbot_2024].
These systems are usually demonstrated as research prototypes in controlled settings, and most do not target the BT engine that production ROS 2 stacks use.
Containers already solve dependency isolation for ROS 2 software [@zhang_distributed_2022; @lim_docker-enabled_2026] and support orchestration of robot fleets [@lampe_robotkube_2023; @lumpp_enabling_2024]; the planner follows this practice.

\autoref{tab:compare} compares GPTMissionPlanner with three tools that connect LLMs to ROS robots: the ROSA agent [@royce_rosa_2024], ROS-LLM [@mower_rosllm_2024], and BTGenBot-2 [@izzo_btgenbot2_2026], the closest tool, which generates BT.CPP XML with a fine-tuned model.

|                          | ROSA                          | ROS-LLM                     | BTGenBot-2               | GPTMissionPlanner                  |
|--------------------------|-------------------------------|-----------------------------|--------------------------|------------------------------------|
| Output                   | ROS tool calls                | Python, JSON, or XML BT     | BT.CPP XML               | BT.CPP XML                         |
| Check before execution   | Tool-level parameter checks   | None described              | XML syntax, action list  | XSD, with LLM retry                |
| Formal verification      | No                            | No                          | No                       | Optional (LTL, SPIN)               |
| Multi-robot allocation   | Not demonstrated              | Not described               | Not described            | Yes, with fleet discovery          |
| Models                   | Cloud or local, tool calling  | Open-source and commercial  | Own fine-tuned 1B model  | Any LiteLLM model, cloud or local  |
| Operator interface       | CLI, web GUI                  | Chat                        | Not described            | Web (text, voice), CLI             |

: Comparison with tools that connect LLMs to ROS robots, from the papers that describe them.\label{tab:compare}

We built a new package instead of contributing to one of these tools.
The outputs of ROSA and ROS-LLM are actions or code, not a mission that a schema can check before execution, and neither tool targets BT.CPP.
BTGenBot-2 is a model, a dataset, and a benchmark; it does not include an operator interface, site context, fleet allocation, or mission delivery to robots.
It is complementary to the planner: because the planner selects its model through configuration, a specialized local model of this kind could replace a general-purpose model, if it accepts the planner's prompts.
BT.CPP and Nav2 execute trees, but they do not generate trees from NL requests.
The design, inspired by the IEEE Standard for Robot Task Representation [@noauthor_ieee_2024], separates NL mission generation from robot control.

# Software design

GPTMissionPlanner is a Python application that runs in one Docker container.
Because ROS 2 depends on the operating system, the container lets any host run the planner.
A FastAPI [@Ramirez_FastAPI] server provides a browser-based operator interface, a second HTTP service lets robots register with the planner, and a TCP link delivers each mission to a receiver on each target robot.
On the robot, a ROS 2 receiver loads each tree into BT.CPP and executes it; our companion robot-side system [@zuzuarregui2026tfr] is one such receiver.
The planning pipeline is a Python core (`MissionPlanner`) that the web server and a command-line interface both call, so the web interface adds no planning logic.
Unit and integration tests (pytest) cover XML validation, the fleet registry and allocator, and the network interface.
The planner has four layers (\autoref{fig:architecture}), and each layer can change without changes to the others:

- *Interface layer.* The web server (1) and the speech-to-text module (2) receive the request. The server transcribes speech in the container with a Whisper model [@radford2023whisper] through faster-whisper [@fasterwhisper].
- *Context layer.* The context module (3) assembles the system prompt from Jinja2 templates: the robot schema, the site description from orchard management (6, 7), and any text context that an integrator supplies.
- *Planning layer.* The LLM agent (4) produces an XML mission (5). The schema check and, if enabled, the formal verification step accept or reject it.
- *Fleet and delivery layer.* A registry tracks the robots that have reported in, and the network module (9) sends each accepted mission to its robot.

![The GPTMissionPlanner architecture: (1) a web server manages the UI session; (2) Whisper transcribes audio to text; (3) the context module prepares the user context configuration; (4) an LLM agent decomposes tasks into (5), an XML plan; (6) orchard management processes spatial data for client-side visualization in (8) and provides the numerical representation in (7); and (9) the network module exchanges HTTP and TCP messages with the user and the robot.\label{fig:architecture}](figures/architecture.png){ width=100% }

Each design decision below has a trade-off.

*The schema is the robot interface.*
The XML schema definition (XSD) of a robot lists its action primitives, constrains the LLM output to missions that BT.CPP can load, and documents the robot interface.
If a mission fails XSD validation, the planner returns the validator error to the LLM and asks for a new plan, up to a configurable number of retries (\autoref{fig:loop}).
An integrator writes the XSD and the BT.CPP nodes once per robot; each node is usually a thin wrapper around an action server that the robot already has.
The schemas for the robots that we use (a Bonsai Robotics Amiga, a Kinova Gen3, and a Clearpath Husky) are released in a separate repository [@gpt_mp_schemas], which the planner includes as a git submodule.

*Formal verification is optional.*
When verification is enabled, a second LLM agent writes a linear temporal logic (LTL) formula for the request, the SPIN model checker [@SPINBook] checks a Promela model of the XML mission against it, and an arbiter LLM checks that the formula agrees with the request (\autoref{fig:loop}).
This step implements the method of @zuzuarregui2025asyouwish.
Verification is necessary only in secure or safety-critical settings.
For routine or low-stakes missions, the operator can plan faster without it, and SPIN and its dependencies are built into the image only when the user requests them.

![The planning loop. The mission agent writes an XML BT, and the XSD check accepts it or returns the validator error to the agent. When formal verification is enabled, a second agent writes an LTL formula, SPIN checks the Promela model of the mission against it, and an arbiter LLM checks that the formula agrees with the request. Every failure is returned to the agent that caused it, up to a configurable number of retries.\label{fig:loop}](figures/planning_loop.png){ width=100% }

*Site context is condensed before it reaches the LLM.*
Raw GPS data for an orchard is too large for the LLM context window.
The operator therefore supplies a GPS polygon of the site, and the planner derives the local geometry from it: working areas, row orientations, perimeter margins, traversal directions, and tree placement.
Only this condensed spatial context goes into the prompt.
Nothing in the planner depends on the orchard module, and deployments for other settings, such as our manipulator, do not use it.

*The model is a configuration choice.*
All LLM calls go through LiteLLM, so one configuration file selects a cloud model, a local model behind an OpenAI-compatible endpoint (for example, vLLM), or an automatic mode that uses the cloud model when the planner has internet access and the local model when it does not.
With a local LLM and in-container speech transcription, the planner needs no internet connection.

*Fleet operation keeps a single-robot fallback.*
A robot registers with the planner's discovery API, giving the schema it executes, and then sends periodic heartbeats with its status, battery level, and position.
For a new request, the planner selects the eligible robots (online, idle, with a known schema and enough battery), and one LLM call divides the mission over them, respecting the action pool of each.
The planner then generates and validates one BT per robot.
With no robot registered, the planner uses a single configured host and port, so robots that do not implement the fleet contract still work.
The repository documents this contract and includes a mock robot that implements it.

*Limitations.*
The planner generates a complete mission before execution and does not use real-time feedback from the robot or its perception during the mission.
Formal verification is not yet available for multi-robot missions; the planner applies only the schema check to them.
LLM outputs are not deterministic, and hosted models change over time; by default, the planner logs each request and generated mission so that results can be inspected.

# Research impact statement

The planner is the software base of a series of studies by our group, and the repository keeps a tag for the version used in each one, so that each study can be reproduced with its own code.
It started as the syntax-feedback planner for precision agriculture of @CarpinLLMSubmitted, was extended to heterogeneous robots in @CARPINIFAC2025 and with formal verification of mission intent in @zuzuarregui2025asyouwish, and it was paired with new robot-side BT execution in @zuzuarregui2026tfr.
Its fleet discovery and allocation are also the base of ongoing work on LLM-assisted task recovery for agricultural robot fleets [@zuzuarregui2027mas].
The tags for these studies are `icra-2025`, `ifac-2025`, `icra-2026`, and `tfr-2026`.
These studies report research results on planning and on robot-side execution; none of them describes the planner software itself.

We used the planner in commercial pistachio and citrus orchards [@zuzuarregui2026tfr].
In the pistachio orchard, it controlled an Amiga mobile platform and a Kinova Gen3 manipulator mounted on it; the operator gave all commands from a phone, and the missions included following the operator, navigation to named trees through their aisles, and arm motions.
The generated missions are in the repository in `examples/orchard_demo/`.
The planner has been used with OpenAI models from GPT-3.5 to GPT-5.6, with Anthropic models from Claude Sonnet 3.7 to Claude Opus 5.5, and with local models such as GPT-OSS 120B.

Agriculture drives our use cases, but the planner does not depend on it: any ROS 2 robot whose capabilities can be described as action servers can use the planner.

# AI usage disclosure

The first author wrote the initial versions of the software without generative AI.
Later development used Claude (Anthropic), mostly Claude Sonnet 4 and Claude Opus 5.5, for code generation, refactoring, tests, and documentation.
Claude Opus 5.5 also helped to edit this paper.
The authors made all core design and architecture decisions, and they reviewed, edited, and validated all AI-assisted code, documentation, and text.
At run time, the software itself calls LLMs to generate missions; Software design describes this use.

# Conflict of interest

The authors declare that they have no known competing financial interests or personal relationships that could have appeared to influence the work reported in this paper.

# Acknowledgements

This work is partially supported by the IoT4Ag Engineering Research Center funded by the National Science Foundation (NSF) under NSF Cooperative Agreement Number EEC-1941529 and under grant CMMI-2326310.
Any opinions, findings, conclusions, or recommendations expressed in this publication are those of the author(s) and do not necessarily reflect the view of the National Science Foundation.
We thank Thomas Morford for his contributions to the software.

# References
