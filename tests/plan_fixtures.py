"""Complete ordinary-plan fixtures; never model-facing call instructions."""


def ordinary_candidates(graph):
    return [{"key": "implementation", "consequences": ["Implement the unchanged current contract."],
             "delta": {"add": [], "retire": []}, "graph": graph}]
