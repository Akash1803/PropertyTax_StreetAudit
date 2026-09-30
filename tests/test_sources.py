import pytest

from streetaudit.sources import GoogleStreetView


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return FakeResponse(self.payload)


OK = {"status": "OK", "pano_id": "abc", "location": {"lat": 11.0, "lng": 76.9}, "date": "2026-02", "copyright": "© Google"}


def test_radius_is_sent_as_a_whole_number():
    # the API answers INVALID_REQUEST to "10.0", which once made a whole ward look empty
    session = FakeSession(OK)
    GoogleStreetView("k", session).nearest_panorama(11.0, 76.9, 10.0)
    assert session.calls[0]["radius"] == 10 and isinstance(session.calls[0]["radius"], int)


def test_panorama_fields_and_ownership():
    pano = GoogleStreetView("k", FakeSession(OK)).nearest_panorama(11.0, 76.9, 10)
    assert pano == {"pano_id": "abc", "lat": 11.0, "lon": 76.9, "date": "2026-02", "own": True}
    user_photo = dict(OK, copyright="© Some User")
    assert GoogleStreetView("k", FakeSession(user_photo)).nearest_panorama(11.0, 76.9, 10)["own"] is False


def test_no_panorama_is_none_but_a_refused_request_is_an_error():
    assert GoogleStreetView("k", FakeSession({"status": "ZERO_RESULTS"})).nearest_panorama(11.0, 76.9, 10) is None
    for status in ("INVALID_REQUEST", "REQUEST_DENIED"):
        with pytest.raises(RuntimeError):
            GoogleStreetView("k", FakeSession({"status": status})).nearest_panorama(11.0, 76.9, 10)
