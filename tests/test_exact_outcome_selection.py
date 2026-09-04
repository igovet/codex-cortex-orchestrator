"""Semantic identity never falls back to normalized or paraphrased titles."""
import pytest

from cortex_runtime.domain_api import _match_outcomes
from cortex_runtime.v12_service import V12ServiceError


ITEM = {"item_ref": "o_0123456789ab", "text": "Check A-B.", "acceptance_criteria": ["Exact acceptance"],
        "constraints": [], "verification_criteria": []}


@pytest.mark.parametrize("name", ["check a-b.", "Check AB", "Check A B.", "Check A-B", "Check  A-B."])
def test_changed_title_is_rejected(name):
    with pytest.raises(V12ServiceError) as failure:
        _match_outcomes([ITEM], [name], path="$.retire")
    assert failure.value.code == "outcome_item_not_found"


def test_exact_title_is_accepted():
    assert _match_outcomes([ITEM], ["Check A-B."], path="$.retire") == [ITEM["item_ref"]]


def test_changed_details_cannot_fall_back_to_matching_title():
    with pytest.raises(V12ServiceError):
        _match_outcomes([ITEM], [{"outcome": "Check A-B.", "acceptance": ["Different acceptance"],
                                 "constraints": [], "verification": []}], path="$.outcomes")
