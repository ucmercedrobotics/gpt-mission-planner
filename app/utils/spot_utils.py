import random

import spot

import re


def regex_spin_to_spot(expression: str) -> str:
    # --- Step 1: Strip 'ltl <label> {' and trailing '}' ---
    expression = expression.strip()
    expression = re.sub(r"^ltl\s+\w+\s*{", "", expression).strip()
    expression = re.sub(r"}\s*$", "", expression).strip()

    # --- Step 2: Wrap in <> if not already ---
    if not expression.startswith("<>"):
        expression = f"<>({expression})"

    return expression


def add_init_state(expression: str) -> str:
    # --- Step 1: Strip 'ltl <label> {' and trailing '}' ---
    expression = expression.strip()
    expression = re.sub(r"^ltl\s+\w+\s*{", "", expression).strip()
    expression = re.sub(r"}\s*$", "", expression).strip()

    expression = f"init && X ({expression})"

    return "ltl mission { " + expression + " }"


def init_state_macro(macros: str) -> str:
    # ensure that the initial state is defined in the LTL macros
    updated_macros: str = ""
    if "#define init" not in macros:
        match = re.search(r"\(.*?==", macros)
        if match is not None:
            first_line = match.group(0)
            init_macro = "#define init " + first_line + " 0)\n"
            updated_macros = init_macro + macros
    return updated_macros


def generate_accepting_run_string(aut) -> str:
    curr = aut.get_init_state_number()
    path = []
    while not aut.state_is_accepting(curr):
        edges = [e for e in aut.out(curr)]

        next = curr
        while next == curr:
            sel_e = random.choice(edges)
            next = sel_e.dst

        # move
        curr = next

        path.append(spot.bdd_format_formula(aut.get_dict(), sel_e.cond))

    return " ".join(path)


def count_ltl_tasks(aut) -> int:
    # count transitions without self-loops
    return sum(1 for s in range(aut.num_states()) for t in aut.out(s) if t.dst != s)


def rename_ltl_macros(task_names_str, globals_str, original_ltl):
    """
    Replace variable names in LTL definitions in order, using tasks by default
    and globals for comparison operations

    Args:
        task_names_str: String containing task declarations (first input part)
        globals_str: String containing global variable declarations (second input part)
        original_ltl: String containing the original #define statements (third input part)

    Returns:
        String with corrected #define statements
    """

    # Extract task names using regex
    task_pattern = r"Task\s+(\w+);"
    tasks = re.findall(task_pattern, task_names_str)

    # Extract global variables using regex
    global_pattern = r"int\s+(\w+);"
    globals_vars = re.findall(global_pattern, globals_str)

    # Split LTL into individual #define statements
    define_lines = [
        line.strip() for line in original_ltl.strip().split("\n") if line.strip()
    ]

    task_index = 0
    global_index = 0
    corrected_lines = []

    for line in define_lines:
        # Extract the content inside parentheses
        paren_match = re.search(r"\(([^)]+)\)", line)
        if not paren_match:
            corrected_lines.append(line)
            continue

        content = paren_match.group(1)

        # Check if this is a global variable comparison (has comparison but NOT .action.actionType)
        has_action_type = ".action.actionType" in content
        has_comparison = bool(re.search(r"[<>]=?", content))  # Only lt, gt, lte, gte

        is_global_comparison = has_comparison and not has_action_type

        if is_global_comparison:
            # This is a global variable comparison - replace with global variable
            if global_index < len(globals_vars):
                # Find the variable name to replace (before the comparison operator)
                var_match = re.search(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", content)
                if var_match:
                    old_var = var_match.group(1)
                    new_line = line.replace(old_var, globals_vars[global_index])
                    corrected_lines.append(new_line)
                    global_index += 1
                else:
                    corrected_lines.append(line)
            else:
                corrected_lines.append(line)
        elif has_action_type:
            # This is a task operation - replace with task name
            if task_index < len(tasks):
                # Find the variable name to replace (before .action.actionType)
                var_match = re.search(
                    r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b(?=\.action\.actionType)", content
                )
                if var_match:
                    old_var = var_match.group(1)
                    new_line = line.replace(old_var, tasks[task_index])
                    corrected_lines.append(new_line)
                    task_index += 1
                else:
                    corrected_lines.append(line)
            else:
                corrected_lines.append(line)
        else:
            # No replacement needed
            corrected_lines.append(line)

    return "\n".join(corrected_lines)
