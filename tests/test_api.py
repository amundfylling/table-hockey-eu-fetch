import unittest

from table_hockey_eu.api import USER_AGENT, ApiClient, NotFound


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        return self.responses.pop(0)


def _client(responses):
    session = FakeSession(responses)
    return ApiClient(min_interval=0, backoff=0, retries=2, session=session), session


class ApiTests(unittest.TestCase):
    def test_retries_server_errors_then_succeeds(self):
        client, session = _client([FakeResponse(503), FakeResponse(429), FakeResponse(200, [{"id": 1}])])
        self.assertEqual(client.tournaments(), [{"id": 1}])
        self.assertEqual(session.urls, ["https://table-hockey.eu/api/tournaments/all"] * 3)
        self.assertEqual(session.headers["User-Agent"], USER_AGENT)

    def test_gives_up_after_retries(self):
        client, _ = _client([FakeResponse(500)] * 3)
        with self.assertRaises(RuntimeError):
            client.stage(5)

    def test_not_found(self):
        client, _ = _client([FakeResponse(404)])
        with self.assertRaises(NotFound):
            client.tournament(99)


if __name__ == "__main__":
    unittest.main()
