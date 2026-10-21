"""The sentence check and the Answer object. No database needed."""

import json

from honest_memory import answer as A
from honest_memory.answer import Answer, check_sentence, template_sentence

NAMES = {"billing-api", "invoice-renderer", "ledger-db", "auth", "Luca Ostrander", "Sana Petrov", "Selma Marsh",
         "Platform", "Payments", "Support", "ADR-0004", "ADR-0009"}


def owner_answer() -> Answer:
    row = {"owner": "Luca Ostrander", "team": "Platform", "system": "billing-api", "since": "2026-09-22T15:28:00Z",
           "previous_owner": "Sana Petrov", "previous_team": "Payments", "previous_until": "2026-09-22T15:28:00Z",
           "source_ids": ["ADR-0009", "msg-0148"], "gate_id": "gd-0900"}
    return Answer(A.ANSWERED_WITH_PATH, "current_owner", {"system": "billing-api"}, [row],
                  [["Luca Ostrander", "OWNS", "billing-api"]],
                  [A.Source("ADR-0009", "ADR-0009: Transfer billing-api ownership", "2026-09-22T15:28:00Z", "doc")])


def empty_answer() -> Answer:
    return Answer(A.NOT_IN_GRAPH, "impact", {"system": "ledger-db", "depth": 4},
                  note="No system depends on ledger-db within 4 hops in the graph.")


def test_accepts_a_sentence_made_of_the_rows():
    ok, why = check_sentence("Luca Ostrander (Platform) owns billing-api since 2026-09-22, per ADR-0009.",
                             owner_answer(), NAMES)
    assert (ok, why) == (True, "ok")


def test_rejects_an_invented_system_name():
    ok, why = check_sentence("Luca Ostrander owns billing-api, which sits behind auth-gateway.", owner_answer(), NAMES)
    assert not ok and "auth-gateway" in why


def test_rejects_a_real_entity_that_is_not_in_the_rows():
    ok, why = check_sentence("Luca Ostrander owns billing-api and also invoice-renderer.", owner_answer(), NAMES)
    assert not ok and "invoice-renderer" in why


def test_rejects_invented_record_ids_and_dates():
    ok, why = check_sentence("Luca Ostrander owns billing-api per ADR-0042.", owner_answer(), NAMES)
    assert not ok and "ADR-0042" in why
    ok, why = check_sentence("Luca Ostrander owns billing-api since 2026-09-21.", owner_answer(), NAMES)
    assert not ok and "2026-09-21" in why
    ok, why = check_sentence("Luca Ostrander owns billing-api, see msg-0999.", owner_answer(), NAMES)
    assert not ok and "msg-0999" in why


def test_rejects_a_person_who_mixes_known_names():
    ok, why = check_sentence("Luca Petrov owns billing-api.", owner_answer(), NAMES)
    assert not ok and "Luca Petrov" in why


def test_plain_hyphenated_words_and_single_word_names_are_not_false_alarms():
    ok, _ = check_sentence("Luca Ostrander is the on-call owner of billing-api.", owner_answer(), NAMES)
    assert ok
    ok, _ = check_sentence("Luca Ostrander owns billing-api; we should follow up and offer customer support.",
                           owner_answer(), NAMES)
    assert ok  # lowercase 'support' is prose, not the Support team


def test_a_team_name_that_is_not_in_the_rows_is_rejected():
    ok, why = check_sentence("Luca Ostrander owns billing-api; ask Support.", owner_answer(), NAMES)
    assert not ok and "Support" in why


def test_no_claim_of_impact_when_nothing_was_found():
    ans = empty_answer()
    ok, why = check_sentence("If ledger-db fails, billing-api is affected.", ans, NAMES)
    assert not ok
    ok, why = check_sentence("Nothing is recorded as depending on ledger-db, which may mean it is not modelled.", ans, NAMES)
    assert (ok, why) == (True, "ok")
    ok, why = check_sentence("No dependency is recorded for ledger-db.", ans, NAMES)
    assert ok


def test_empty_and_whitespace_sentences_are_rejected():
    assert check_sentence("   ", owner_answer(), NAMES)[0] is False


def test_names_in_the_call_arguments_are_allowed_for_unknown_entity():
    ans = Answer(A.UNKNOWN_ENTITY, "current_owner", {"system": "payment-service"},
                 note="There is no system named 'payment-service' in this memory, so no query was run.")
    ok, _ = check_sentence("There is no system called payment-service, so I cannot say who owns it.", ans, NAMES)
    assert ok
    ok, why = check_sentence("payment-service is owned by Luca Ostrander.", ans, NAMES)
    assert not ok


def test_template_sentences_describe_rows_only():
    s = template_sentence(owner_answer())
    assert s.startswith("Luca Ostrander (Platform) owns billing-api, since 2026-09-22; before that Sana Petrov (Payments)")
    assert check_sentence(s, owner_answer(), NAMES)[0]
    assert template_sentence(empty_answer()) == "No system depends on ledger-db within 4 hops in the graph."


def test_timestamps_accept_epoch_and_iso():
    assert A.iso_ts(1790090880) == "2026-09-22T15:28:00Z"
    assert A.iso_ts("2026-09-22T15:28:00Z") == "2026-09-22T15:28:00Z"
    assert A.iso_ts(None) == "" and A.day(1790090880) == "2026-09-22"


def test_render_path_shows_direction():
    path = ["Sana Petrov", "WORKED_ON", "ledger-db", "<-WORKED_ON", "Priya Farrow"]
    assert A.render_path(path) == "Sana Petrov -WORKED_ON-> ledger-db <-WORKED_ON- Priya Farrow"
    assert A.render_path(["a"]) == "a"


def test_answer_serialises_to_json():
    blob = json.dumps(owner_answer().to_dict())
    assert '"outcome": "answered_with_path"' in blob and '"id": "ADR-0009"' in blob


def test_gate_outcomes_map_to_what_agents_see():
    assert A.GATE_OUTCOMES == {"ACCEPT": "accepted", "CONFIRM": "confirmed", "SUPERSEDE": "superseded",
                               "REJECT": "refused", "CLOSE": "closed"}
    assert set(A.OUTCOMES) == set(A.READ_OUTCOMES) | set(A.WRITE_OUTCOMES) and len(A.OUTCOMES) == 9
