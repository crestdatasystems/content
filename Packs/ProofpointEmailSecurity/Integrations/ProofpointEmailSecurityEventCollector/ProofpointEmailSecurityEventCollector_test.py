import uuid
from contextlib import ExitStack, contextmanager

import ProofpointEmailSecurityEventCollector
import pytest
from freezegun import freeze_time
from ProofpointEmailSecurityEventCollector import (
    EVENT_TYPES,
    Connection,
    DemistoException,
    EventConnection,
    datetime,
    demisto,
    fetch_events,
    json,
    long_running_execution_command,
    perform_long_running_loop,
    timedelta,
    websocket_connections,
)

CURRENT_TIME: datetime | None = None

EVENTS = [
    {"ts": "2023-08-16T13:24:12.147573+0100", "message": "Test message 1", "id": 1},
    {"ts": "2023-08-14T13:24:12.147573+0200", "message": "Test message 2", "id": 2},
    {"ts": "2023-08-12T13:24:11.147573+0000", "message": "Test message 3", "guid": 3},
]


def is_interval_passed(fetch_start_time: datetime, fetch_interval: int) -> bool:
    global CURRENT_TIME
    if not CURRENT_TIME:
        CURRENT_TIME = fetch_start_time
    return fetch_start_time + timedelta(seconds=fetch_interval) < CURRENT_TIME


@pytest.fixture
def connection():
    # Set up a mock connection
    return MockConnection()


class MockConnection(Connection):
    def __init__(
        self,
    ):
        global CURRENT_TIME
        self.id = uuid.uuid4()
        self.events = EVENTS
        self.index = 0
        self.pongs = 0
        self.create_time = datetime.now()

    def recv(self, timeout):
        global CURRENT_TIME
        # pretend to sleep for 4 seconds
        assert CURRENT_TIME
        CURRENT_TIME += timedelta(seconds=4)

        if self.index >= len(self.events):
            raise TimeoutError
        event = self.events[self.index]
        self.index += 1
        return json.dumps(event)

    def pong(self):
        self.pongs += 1


def test_fetch_events(mocker, connection):
    """
    Given:
        A connection to the websocket

    When:
        Calling fetch_events function to get events from the websocket connection

    Then:
        - Ensure that the function returns the events from the websocket connection
        - Ensure that the function converts the timestamp to UTC
        - Ensure that the function returns the events collected in the interval until events finished
    """

    # Mock the connect method to return the mock connection
    mocker.patch.object(EventConnection, "connect", return_value=connection)

    # We set fetch_interval to 7 to get this first two events (as we "wait" 4 seconds between each event)
    fetch_interval = 7
    event_connection = EventConnection(event_type="message", url="wss://testing", headers={})
    mocker.patch.object(ProofpointEmailSecurityEventCollector, "is_interval_passed", side_effect=is_interval_passed)
    debug_logs = mocker.patch.object(demisto, "debug")
    events = fetch_events(
        connection=event_connection, fetch_interval=fetch_interval, integration_context={}, should_skip_sleeping=[]
    )

    assert len(events) == 2
    assert events[0]["message"] == "Test message 1"
    assert events[0]["_time"] == "2023-08-16T12:24:12.147573+00:00"
    assert events[0]["event_type"] == "message"
    assert events[1]["message"] == "Test message 2"
    assert events[1]["_time"] == "2023-08-14T11:24:12.147573+00:00"
    assert events[1]["event_type"] == "message"

    debug_logs.assert_any_call("The fetched events ids are: 1, 2")
    # Now we want to freeze the time, so we will get the next interval
    with freeze_time(CURRENT_TIME):
        debug_logs = mocker.patch.object(demisto, "debug")
        events = fetch_events(
            connection=event_connection, fetch_interval=fetch_interval, integration_context={}, should_skip_sleeping=[]
        )
    assert len(events) == 1
    assert events[0]["message"] == "Test message 3"
    assert events[0]["_time"] == "2023-08-12T13:24:11.147573+00:00"
    assert events[0]["event_type"] == "message"

    debug_logs.assert_any_call("The fetched events ids are: 3")


@freeze_time("2023-08-16T13:24:12.147573+0100")
def test_connects_to_websocket(mocker):
    """
    Given:
        - A host with cluster id and api key to connect to

    When:
        - Creating a connection to the websocket

    Then:
        - Ensure that the function connects to the websocket with the correct url
    """
    # Mock the connect function from websockets.sync.client
    connect_mock = mocker.patch.object(ProofpointEmailSecurityEventCollector, "connect")

    # Call the websocket_connections function without since_time and to_time
    with websocket_connections("wss://host", "cluster_id", "api_key", since_time="2023-08-16T12:24:12.147573"):
        pass

    assert connect_mock.call_count == len(EVENT_TYPES)
    for event_type in EVENT_TYPES:
        connect_mock.assert_any_call(
            f"wss://host/v1/stream?cid=cluster_id&type={event_type}&sinceTime=2023-08-16T12:24:12.147573",
            additional_headers={"Authorization": "Bearer api_key"},
        )

    connect_mock = mocker.patch.object(ProofpointEmailSecurityEventCollector, "connect")

    # Call the websocket_connections function with since_time and to_time
    with websocket_connections(
        "wss://host", "cluster_id", "api_key", since_time="2023-08-14T12:24:12.147573", to_time="2023-08-16T12:24:12.147573"
    ):
        pass

    assert connect_mock.call_count == len(EVENT_TYPES)
    for event_type in EVENT_TYPES:
        connect_mock.assert_any_call(
            f"wss://host/v1/stream?cid=cluster_id&type={event_type}&sinceTime=2023-08-14T12:24:12.147573&toTime=2023-08-16T12:24:12.147573",
            additional_headers={"Authorization": "Bearer api_key"},
        )


def test_handle_failures_of_send_events(mocker, capfd):
    """
    Given:
        - A connection to the websocket, and events are fetched from the socket

    When:
        - Sending events to XSIAM are failing.

    Then:
        - Add the failing events to the context, and try again in the next run.
    """

    def fetch_events_mock(connection: EventConnection, fetch_interval: int, integration_context, should_skip_sleeping):
        if connection.event_type == "message":
            return EVENTS[:2]
        return EVENTS[2:]

    def sends_events_to_xsiam_mock(events, **kwargs):
        raise DemistoException("Message")

    mocker.patch.object(ProofpointEmailSecurityEventCollector, "fetch_events", side_effect=fetch_events_mock)
    mocker.patch.object(ProofpointEmailSecurityEventCollector, "send_events_to_xsiam", side_effect=sends_events_to_xsiam_mock)

    # Mock the connect method to return the mock connection
    mocker.patch.object(EventConnection, "connect", return_value=MockConnection())
    with capfd.disabled():
        perform_long_running_loop(
            [
                EventConnection("message", url="wss://test", headers={}),
                EventConnection("maillog", url="wss://test", headers={}),
            ],
            60,
            [],
        )
    context = demisto.getIntegrationContext()
    assert context["message"] == EVENTS[:2]
    assert context["maillog"] == EVENTS[2:]

    second_try_send_events_mock = mocker.patch.object(ProofpointEmailSecurityEventCollector, "send_events_to_xsiam")
    with capfd.disabled():
        perform_long_running_loop(
            [
                EventConnection("message", url="wss://test", headers={}),
                EventConnection("maillog", url="wss://test", headers={}),
            ],
            60,
            [],
        )
    context = demisto.getIntegrationContext()
    # check the context is cleared
    for event in EVENTS:
        assert str(event) not in str(context)
    # check that the events failed events were sent to xsiam
    for event in EVENTS:
        assert event in second_try_send_events_mock.call_args_list[0][0][0]


def test_heartbeat(mocker, connection):
    """
    Given:
        - A connection object with scarce messages

    When:
        - The long running execution loop runs

    Then:
        - Periodic keep-alive messages (pongs) are sent to the websocket connection to prevent it from closing.

    """
    idle_timeout = 3

    @contextmanager
    def mock_websocket_connections(
        host, cluster_id, api_key, since_time=None, to_time=None, fetch_interval=60, event_types=["audit"]
    ):
        with ExitStack():
            yield [
                EventConnection("audit", url="wss://test", headers={}, fetch_interval=fetch_interval, idle_timeout=idle_timeout)
            ]

    def mock_perform_long_running_loop(connections, interval, should_skip_sleeping):
        # This mock will raise exceptions to stop the long running loop
        # StopIteration exception marks success
        connection = connections[0].connection
        if connection.pongs:
            raise StopIteration(f"Sent {connections[0].connection.pongs} pongs")
        if datetime.now() > connection.create_time + timedelta(seconds=idle_timeout + 2):
            # Heartbeat should've been sent already
            raise TimeoutError(f"No heartbeat sent within {idle_timeout} seconds")

    mocker.patch.object(ProofpointEmailSecurityEventCollector, "websocket_connections", side_effect=mock_websocket_connections)
    mocker.patch.object(
        ProofpointEmailSecurityEventCollector, "perform_long_running_loop", side_effect=mock_perform_long_running_loop
    )
    mocker.patch.object(EventConnection, "connect", return_value=connection)
    mocker.patch.object(ProofpointEmailSecurityEventCollector, "support_multithreading")
    mocker.patch.object(demisto, "error", side_effect=StopIteration("Interrupted execution"))  # to break endless loop.

    with pytest.raises(StopIteration):
        long_running_execution_command("host", "cid", "key", 60, ["audit"])

    assert connection.pongs > 0


def test_recovering_execution(mocker, connection):
    """
    Running long_running_execution_command and throwing error every time mock_perform_long_running_loop is
    called to ensure it is being called more than once (i.e, can recover from the failure)
    """
    idle_timeout = 3

    execution_count = 0

    def count_iterations(msg):
        nonlocal execution_count
        execution_count += 1
        if execution_count > 1:
            raise StopIteration("Interrupted execution")

    @contextmanager
    def mock_websocket_connections(
        host, cluster_id, api_key, since_time=None, to_time=None, fetch_interval=60, event_types=["audit"]
    ):
        with ExitStack():
            yield [
                EventConnection("audit", url="wss://test", headers={}, fetch_interval=fetch_interval, idle_timeout=idle_timeout)
            ]

    def mock_perform_long_running_loop(connections, interval, should_skip_sleeping):
        # This mock will raise exceptions to stop the long running loop
        # StopIteration exception marks success
        raise StopIteration(f"Sent {connections[0].connection.pongs} pongs")

    mocker.patch.object(ProofpointEmailSecurityEventCollector, "websocket_connections", side_effect=mock_websocket_connections)
    mocker.patch.object(
        ProofpointEmailSecurityEventCollector, "perform_long_running_loop", side_effect=mock_perform_long_running_loop
    )
    mocker.patch.object(EventConnection, "connect", return_value=connection)
    mocker.patch.object(ProofpointEmailSecurityEventCollector, "support_multithreading")
    demisto_error_mocker = mocker.patch.object(demisto, "error", side_effect=count_iterations)  # to break endless loop.

    with pytest.raises(StopIteration):
        long_running_execution_command("host", "cid", "key", 60, ["audit"])

    assert demisto_error_mocker.call_count > 1
