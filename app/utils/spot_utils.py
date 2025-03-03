import random

import spot


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
