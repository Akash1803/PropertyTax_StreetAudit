import json

from streetaudit.llm import parse_answer


def response(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_parse_answer_object():
    assert parse_answer(response(json.dumps({"classification": {}, "views": []}))) == {"classification": {}, "views": []}


def test_parse_answer_unwraps_a_single_item_list():
    assert parse_answer(response(json.dumps([{"classification": {}}]))) == {"classification": {}}


def test_parse_answer_rejects_bad_answers():
    assert parse_answer(response("not json")) is None
    assert parse_answer(response(json.dumps({"something": "else"}))) is None
    assert parse_answer({"candidates": []}) is None
