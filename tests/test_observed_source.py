"""State-service client tests: captured replies (tests/fixtures/observed/README.md) through the parser, and a local HTTP
stub for status codes, dropped connections, retries and deadlines. Nothing here reaches the real services."""

import json
import threading
import time
from collections.abc import Callable
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd
import pytest

import observed_source as src
from observed_source import DeadlineExceeded, NoData, RateLimited, ServiceError, StateClient, StationNotFound, TransientError, UnexpectedResponse

FIXTURES = Path(__file__).parent / "fixtures/observed"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def parse(name: str, state: str, site: str, var: str = "F") -> src.Trace:
    return src.parse_traces(state, src.decode_body(state, fixture(name), "application/json"), site, var)


# --- parsing captured replies ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "state", "site", "var", "values", "quality", "units"),
    [
        ("nsw_412033_flow.json", "NSW", "412033", "F", [0.0, 0.0, 0.0, 0.0], 140, "ML/d"),
        ("nsw_412033_level.json", "NSW", "412033", "L", [1.12, 1.12, 1.12, 1.12], 11, "m"),
        ("vic_405201_flow.json", "VIC", "405201", "F", [5004.16, 5025.07, 5025.97, 5014.02], 2, "ML/d"),
        ("qld_422213A_flow.json", "QLD", "422213A", "F", None, None, "ML/d"),
    ],
)
def test_live_replies_parse_to_the_standard_columns(name, state, site, var, values, quality, units):
    trace = parse(name, state, site, var)
    frame = trace.frame

    assert list(frame.columns) == src.RAW_COLUMNS
    assert frame["DATETIME"].dt.strftime("%Y-%m-%d").tolist() == ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]
    assert frame["DATASOURCEID"].unique().tolist() == [state] and frame["SITEID"].unique().tolist() == [site]
    assert frame["VALUE"].dtype == "float64" and frame["QUALITYCODE"].dtype == "int64"
    assert trace.units == units and frame["UNITS"].unique().tolist() == [units]
    if values is not None:
        assert frame["VALUE"].tolist() == values and frame["QUALITYCODE"].unique().tolist() == [quality]


def test_multi_site_reply_returns_only_the_requested_trace():
    trace = parse("nsw_412033_412002_flow.json", "NSW", "412002")

    assert trace.frame["SITEID"].unique().tolist() == ["412002"]
    assert trace.frame["VALUE"].tolist() == [1419.85, 1166.383]
    with pytest.raises(UnexpectedResponse, match="no trace for 410001"):
        parse("nsw_412033_412002_flow.json", "NSW", "410001")


def test_unavailable_days_are_dropped_not_read_as_zero_flow():
    """412033's record starts in May 1938 and has a gap from 31 Jul 1942; the service fills both with "0.000" (q 255/201)."""
    trace = parse("nsw_412033_flow_full_record_trimmed.json", "NSW", "412033")

    assert trace.frame["DATETIME"].dt.strftime("%Y-%m-%d").tolist() == ["1938-05-02", "1938-05-03", "1942-07-30", "2026-10-18"]
    assert trace.frame["VALUE"].tolist() == [18.957, 18.868, 384.848, 141.37]
    assert trace.dropped_unavailable == 7 and trace.dropped_invalid == 0
    assert not trace.frame["QUALITYCODE"].isin([201, 255]).any()


def test_unavailable_codes_are_read_from_the_replys_own_descriptions():
    """VIC leaves 255's description blank and codes lost data 151 ("Data lost due to natural causes")."""
    trace = parse("vic_405201_flow_full_record_trimmed.json", "VIC", "405201")

    assert trace.frame["DATETIME"].dt.strftime("%Y-%m-%d").tolist() == ["1974-12-06"]
    assert trace.frame["VALUE"].tolist() == [7202.03]
    assert trace.dropped_unavailable == 5


def test_messy_points_are_sorted_deduplicated_and_counted():
    trace = parse("synthetic_messy_points.json", "NSW", "412033")

    assert trace.frame["DATETIME"].dt.strftime("%Y-%m-%d").tolist() == ["2020-01-01", "2020-01-02"]
    assert trace.frame["VALUE"].tolist() == [1.0, 9.0]  # the later of two 2 Jan points wins
    assert trace.frame["QUALITYCODE"].tolist() == [140, 130]
    assert trace.dropped_invalid == 3  # blank value, "n/a", ISO timestamp
    assert trace.dropped_unavailable == 1  # q 1000, the getter's >= 999 cutoff


def test_legacy_underscore_return_key_is_accepted():
    trace = parse("synthetic_legacy_underscore_return.json", "QLD", "422213A")
    assert trace.frame["VALUE"].tolist() == [12.5, 13.75]


@pytest.mark.parametrize(
    ("name", "state", "site", "error", "match"),
    [
        ("nsw_no_data_in_period.json", "NSW", "412033", NoData, "No data within specified period"),
        ("nsw_invalid_station.json", "NSW", "999999", StationNotFound, "error 20: Invalid station/datasource"),
        ("qld_invalid_station.json", "QLD", "422030", StationNotFound, "QLD service for 422030"),
        ("nsw_missing_version.json", "NSW", "412033", ServiceError, 'error 120: Missing top-level "version" item'),
        ("synthetic_queue_full.json", "NSW", "412033", RateLimited, "Too many requests in queue"),
        ("synthetic_unexpected_units.json", "NSW", "412033", UnexpectedResponse, "'Cumecs'.*expected ML/d"),
        ("synthetic_compressed_trace.json", "NSW", "412033", UnexpectedResponse, "compressed trace"),
    ],
)
def test_error_replies_raise_typed_errors(name, state, site, error, match):
    with pytest.raises(error, match=match):
        parse(name, state, site)


def test_level_request_rejects_a_flow_trace():
    with pytest.raises(UnexpectedResponse, match="expected m"):
        parse("nsw_412033_flow.json", "NSW", "412033", "L")


@pytest.mark.parametrize(
    ("body", "content_type", "error", "match"),
    [
        (fixture("synthetic_maintenance_200.html"), "text/html", UnexpectedResponse, "Scheduled maintenance - WaterNSW"),
        (fixture("synthetic_maintenance_200.html"), "application/json", UnexpectedResponse, "HTML page"),
        (fixture("synthetic_truncated.json"), "application/json", TransientError, "malformed JSON"),
        (fixture("qld_iis_client_timeout.txt"), "text/plain", TransientError, "text instead of JSON: 'The page cannot be displayed"),
        (b"  \r\n", "application/json", TransientError, "empty body"),
        (b"[]", "application/json", UnexpectedResponse, "JSON list"),
    ],
)
def test_bodies_that_are_not_a_reply_object_are_rejected(body, content_type, error, match):
    with pytest.raises(error, match=match):
        src.decode_body("NSW", body, content_type)


def test_record_start_is_the_earliest_level_or_flow_period():
    data = src.decode_body("NSW", fixture("nsw_412033_variable_list.json"))

    assert src.parse_record_start("NSW", data, "412033", "F") == date(1938, 5, 1)
    assert src.parse_record_start("NSW", data, "412033", "L") == date(1938, 5, 1)
    with pytest.raises(StationNotFound, match="no variables for 999999"):
        src.parse_record_start("NSW", src.decode_body("NSW", fixture("nsw_invalid_station_variable_list.json")), "999999", "F")


# --- request construction -------------------------------------------------------------------------------------------


def payload_of(url: str) -> dict:
    return json.loads(unquote(urlsplit(url).query))


def test_request_url_is_percent_encoded_getter_payload():
    url = src.request_url("QLD", ["422213A"], date(2020, 1, 1), date(2020, 1, 4), "F")

    assert url.startswith("https://water-monitoring.information.qld.gov.au/cgi/webservice.pl?%7B%22params%22")
    assert not set(urlsplit(url).query) & set('{}":, ')
    assert payload_of(url) == {
        "params": {
            "site_list": "422213A",
            "start_time": "20200101000000",
            "varfrom": "100.00",
            "interval": "day",
            "varto": "141.00",
            "datasource": "AT",
            "end_time": "20200104000000",
            "data_type": "mean",
            "multiplier": "1",
        },
        "function": "get_ts_traces",
        "version": "2",
    }
    assert {k: payload_of(src.request_url("NSW", ["412033"], date(2020, 1, 1), date(2020, 1, 1), "L"))["params"][k] for k in ("varfrom", "varto")} == {
        "varfrom": "100",
        "varto": "100",
    }
    with pytest.raises(ValueError):
        src.request_url("NSW", ["412033"], date(2020, 1, 2), date(2020, 1, 1), "F")
    with pytest.raises(ValueError):
        src.request_url("NSW", ["412033"], date(2020, 1, 1), date(2020, 1, 1), "SV")


@pytest.mark.observed
@pytest.mark.parametrize(("state", "var"), [("NSW", "F"), ("NSW", "L"), ("VIC", "F"), ("VIC", "L"), ("QLD", "F"), ("QLD", "L")])
def test_payload_matches_what_mdba_gauge_getter_sends(monkeypatch, state, var):
    """Drift guard: if a getter release changes an endpoint, data source or variable code, this fails."""
    getter = pytest.importorskip("mdba_gauge_getter.gauge_getter")
    sent = []

    class Reply:
        status_code, content = 200, b'{"error_num":0,"return":{"traces":[]}}'

    def get(url, *args, **kwargs):
        sent.append(url)
        return Reply()

    monkeypatch.setattr(getter.requests, "get", get)
    getter.call_state_api(state, ["412033"], date(2020, 1, 1), date(2020, 1, 4), src.DATASOURCES[state], var, "day", "mean")
    ours = src.request_url(state, ["412033"], date(2020, 1, 1), date(2020, 1, 4), var)

    assert sent[0].split("?", 1)[0] == ours.split("?", 1)[0]
    assert json.loads(sent[0].split("?", 1)[1]) == payload_of(ours)


# --- HTTP behaviour against a local stub ----------------------------------------------------------------------------

Reply = tuple[int, dict[str, str], bytes] | Callable[[BaseHTTPRequestHandler], None]


class Upstream:
    def __init__(self) -> None:
        self.replies: list[Reply] = []
        self.requests: list[tuple[str, dict[str, str]]] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                upstream.requests.append((self.path, dict(self.headers)))
                reply = upstream.replies.pop(0) if upstream.replies else (500, {}, b"no reply queued")
                if callable(reply):
                    return reply(self)
                status, headers, body = reply
                self.send_response(status)
                for key, value in ({"Content-Type": "application/json"} | headers).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/cgi/webservice.exe"


@pytest.fixture
def upstream():
    stub = Upstream()
    yield stub
    stub.server.shutdown()
    stub.server.server_close()


@pytest.fixture
def client(upstream):
    sleeps: list[float] = []
    made = StateClient({"NSW": upstream.url}, sleep=sleeps.append)
    made.sleeps = sleeps  # type: ignore[attr-defined]
    return made


def ok(name: str = "nsw_412033_flow.json") -> Reply:
    return (200, {}, fixture(name))


def fetch(client, site: str = "412033", **kwargs) -> src.Trace:
    return client.fetch("NSW", site, date(2020, 1, 1), date(2020, 1, 4), "F", **kwargs)


def drop_connection(handler: BaseHTTPRequestHandler) -> None:
    handler.close_connection = True
    handler.connection.shutdown(2)


def test_success_sends_json_accept_header_and_getter_payload(client, upstream):
    upstream.replies = [ok()]
    trace = fetch(client)

    path, headers = upstream.requests[0]
    assert len(trace.frame) == 4
    assert headers["Accept"] == "application/json" and headers["User-Agent"].startswith("ewr-analysis-dashboard")
    assert json.loads(unquote(urlsplit(path).query))["params"]["datasource"] == "CP"


def test_server_errors_back_off_and_retry(client, upstream):
    upstream.replies = [(503, {"Content-Type": "text/html"}, b"<html><title>503 Service Unavailable</title></html>"), (500, {}, b"{}"), ok()]

    assert len(fetch(client, retries=2).frame) == 4
    assert len(upstream.requests) == 3
    assert len(client.sleeps) == 2 and 0.25 <= client.sleeps[0] <= 0.5 and 0.5 <= client.sleeps[1] <= 1.0


def test_gateway_html_is_reported_after_the_last_retry(client, upstream):
    upstream.replies = [(502, {"Content-Type": "text/html"}, fixture("synthetic_gateway_502.html"))] * 3

    with pytest.raises(TransientError, match="HTTP 502: 502 Bad Gateway"):
        fetch(client, retries=2)
    assert len(upstream.requests) == 3


def test_dropped_connection_and_empty_body_are_retried(client, upstream):
    upstream.replies = [drop_connection, (200, {}, b""), ok()]

    assert len(fetch(client, retries=2).frame) == 4
    assert len(upstream.requests) == 3


@pytest.mark.parametrize(
    ("reply", "match"),
    [
        ((429, {"Retry-After": "30"}, b"Too Many Requests"), r"HTTP 429, retry after 30s"),
        ((408, {}, b"Too many requests in queue (21), limit is 20"), r"HTTP 408"),
        ((200, {}, fixture("synthetic_queue_full.json")), "Too many requests in queue"),
    ],
)
def test_rate_limits_are_not_retried(client, upstream, reply, match):
    upstream.replies = [reply, ok()]

    with pytest.raises(RateLimited, match=match):
        fetch(client, retries=2)
    assert len(upstream.requests) == 1


@pytest.mark.parametrize(
    ("reply", "site", "error"),
    [
        ((404, {"Content-Type": "text/html"}, b"<html>Not Found</html>"), "412033", ServiceError),
        ((200, {"Content-Type": "text/html"}, fixture("synthetic_maintenance_200.html")), "412033", UnexpectedResponse),
        ((200, {}, fixture("nsw_invalid_station.json")), "999999", StationNotFound),
        ((200, {}, fixture("nsw_no_data_in_period.json")), "412033", NoData),
        ((200, {}, fixture("nsw_invalid_station.json")), "412033", UnexpectedResponse),  # a reply for another site
    ],
)
def test_permanent_failures_are_not_retried(client, upstream, reply, site, error):
    upstream.replies = [reply, ok()]

    with pytest.raises(error):
        fetch(client, site=site, retries=2)
    assert len(upstream.requests) == 1


def test_a_slow_drip_reply_stops_at_the_deadline(client, upstream):
    def drip(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", "100000")
        handler.end_headers()
        try:
            for _ in range(40):
                handler.wfile.write(b" " * 70000)
                handler.wfile.flush()
                time.sleep(0.1)
        except OSError:
            pass

    upstream.replies = [drip]
    started = time.monotonic()
    with pytest.raises(DeadlineExceeded):
        fetch(client, timeout=0.5)
    assert time.monotonic() - started < 2.0


def test_a_server_that_never_answers_stops_at_the_deadline(client, upstream):
    upstream.replies = [lambda handler: time.sleep(3)]
    started = time.monotonic()

    with pytest.raises(DeadlineExceeded):
        fetch(client, timeout=0.5, retries=2)
    assert time.monotonic() - started < 2.0


def test_requests_start_at_the_stations_first_recorded_day(client, upstream):
    upstream.replies = [ok("nsw_412033_variable_list.json"), ok(), ok()]

    client.fetch("NSW", "412033", date(1800, 1, 1), date(2020, 1, 4), "F", clamp_to_record=True)
    client.fetch("NSW", "412033", date(1800, 1, 1), date(2020, 1, 4), "F", clamp_to_record=True)
    sent = [json.loads(unquote(urlsplit(path).query)) for path, _ in upstream.requests]

    assert [request["function"] for request in sent] == ["get_variable_list", "get_ts_traces", "get_ts_traces"]  # the period is memoised
    assert sent[1]["params"]["start_time"] == "19380501000000"


def test_a_period_before_the_record_is_empty_without_a_trace_request(client, upstream):
    upstream.replies = [ok("nsw_412033_variable_list.json")]

    with pytest.raises(NoData, match="record starts 1938-05-01"):
        client.fetch("NSW", "412033", date(1900, 1, 1), date(1900, 1, 4), "F", clamp_to_record=True)
    assert len(upstream.requests) == 1


def test_an_unusable_variable_list_falls_back_to_the_requested_start(client, upstream):
    upstream.replies = [ok("nsw_missing_version.json"), ok()]

    client.fetch("NSW", "412033", date(2020, 1, 1), date(2020, 1, 4), "F", clamp_to_record=True)

    assert json.loads(unquote(urlsplit(upstream.requests[1][0]).query))["params"]["start_time"] == "20200101000000"


def test_unknown_state_is_a_station_not_found(client):
    with pytest.raises(StationNotFound, match="No state service for SA"):
        client.fetch("SA", "A4261002", date(2020, 1, 1), date(2020, 1, 4), "F")


def test_parsed_frame_round_trips_through_the_standard_frame(client, upstream):
    """Placeholder zeros must reach py_ewr as gaps (NaN), not flow."""
    import observed

    upstream.replies = [ok("nsw_412033_flow_full_record_trimmed.json")]
    frame = client.fetch("NSW", "412033", date(1900, 1, 1), date(2026, 10, 19), "F").frame
    standard = observed.to_standard_frame({("412033", "F"): frame}).set_index("Date")["412033_flow"]

    assert standard.index[0] == "1938-05-02" and standard.index[-1] == "2026-10-18"
    assert pd.isna(standard["1942-07-31"]) and pd.isna(standard["2000-01-01"])
    assert (standard.dropna() > 0).all()
