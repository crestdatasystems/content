import json
from datetime import datetime

from requests import Response

import demistomock as demisto
import pytest
from CommonServerPython import DemistoException, EntryFormat, EntryType, BaseClient
from freezegun import freeze_time
from SaasSecurity import LIMIT_DEFAULT, LIMIT_MAX, LIMIT_MIN, Client, validate_limit


def util_load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.loads(f.read())


@pytest.fixture()
def client(mocker):
    client = Client(base_url="http://base_url", verify=False, client_id="client_id", client_secret="client_secret", proxy=False)

    mocker.patch.object(Client, "get_access_token", return_value="access_token")
    return client


@pytest.fixture()
def demisto_mocker(mocker):
    mocker.patch.object(
        demisto,
        "params",
        return_value={
            "url": "http://base_url/",
            "credentials": {"identifier": "client_id", "password": "client_secret"},
            "first_fetch": "3 days",
            "max_fetch": "50",
            "state": "Open",
            "severity": "High,Low",
            "status": "Assigned",
            "mirror_direction": "Incoming And Outgoing",
        },
    )
    mocker.patch.object(demisto, "getLastRun", return_value={"last_run_time": "2021-08-25T13:51:03.247Z"})
    mocker.patch.object(demisto, "incidents")
    mocker.patch.object(demisto, "results")


@freeze_time("2021-08-24 18:04:00")
def test_get_passed_mins():
    """
    Tests get_passed_mins helper function.
    Using @freeze_time decorator in order to make the datetime.now() method a permanent value.
    """
    from SaasSecurity import get_passed_mins

    start_time = datetime.now()
    end_time_str = start_time.replace(hour=16).timestamp()
    expected_time_delta = 120
    result = get_passed_mins(start_time, end_time_str)
    assert expected_time_delta == result


@pytest.mark.parametrize(
    "integration_context, expected_token, force_generate",
    [
        ({"access_token": "valid_access_token", "time_issued": 1629827440.0}, "valid_access_token", False),
        ({"access_token": "expired_access_token", "time_issued": 1629901436.0}, "new_access_token", False),
        ({}, "new_access_token", False),
        ({"access_token": "valid_access_token", "time_issued": 1629827440.0}, "new_access_token", True),
    ],
)
@freeze_time("2020-08-24 18:04:21.446809")
def test_get_access_token(mocker, integration_context, expected_token, force_generate):
    """
    Configures mocker instance and patches the client's _http_request to generate access token.

    Use-cases:
    1. There is a valid access token in the integration context.
    2. The access token saved in the integration context is no longer valid.
    3. There is no access token in the integration context.
    4. There is a valid access token in the integration context, but the argument force_generate was given.
    """
    client = Client(base_url="http://base_url", verify=False, client_id="client_id", client_secret="client_secret", proxy=False)
    mocker.patch.object(demisto, "getIntegrationContext", return_value=integration_context)
    mocker.patch.object(client, "_http_request", return_value={"access_token": "new_access_token"})
    access_token = client.get_access_token(force_generate)
    assert access_token == expected_token


def test_convert_to_xsoar_incident():
    """
    Given:
        - A full incident from the Saas Security platform
    When:
        - Fetching incidents
    Then:
        - Returns xsoar incident
    """
    from SaasSecurity import convert_to_xsoar_incident

    incident = util_load_json("test_data/get-incident-by-id.json")
    expected = {"name": "Saas Security: SP0605 copy 6.java", "occurred": "2021-08-03T20:25:15Z", "rawJSON": json.dumps(incident)}
    xsoar_incident = convert_to_xsoar_incident(incident)
    assert xsoar_incident == expected


def test_convert_to_xsoar_incident_without_occurred():
    """
    Given:
        - An incident without the created_at field from the Saas Security platform
    When:
        - Fetching incidents
    Then:
        - Returns xsoar incident
    """
    from SaasSecurity import convert_to_xsoar_incident

    incident = util_load_json("test_data/get-incident-by-id.json")
    incident["created_at"] = None
    expected = {"name": "Saas Security: SP0605 copy 6.java", "occurred": None, "rawJSON": json.dumps(incident)}
    xsoar_incident = convert_to_xsoar_incident(incident)
    assert xsoar_incident == expected


@pytest.mark.parametrize("last_run", ("2018-10-01T20:22:35.000Z", None))
@freeze_time("2021-08-24 18:04:00")
def test_fetch_incidents(mocker, client, requests_mock, demisto_mocker, last_run):
    """
    Configures mocker instance, requests_mock, uses the demisto_mocker fixture.
        Given:
            - Fetch params
        When:
            - Fetching incidents
        Then:
            - Returns list of xsoar incidents and filter by created time
    """
    from SaasSecurity import main

    get_incidents = util_load_json("test_data/get-incidents.json")
    incidents_for_fetch = util_load_json("test_data/fech_incident_data.json")
    mocker.patch.object(demisto, "command", return_value="fetch-incidents")
    mocker.patch.object(demisto, "getLastRun", return_value={"last_run_time": last_run})
    requests_mock.get("http://base_url/incident/api/incidents/delta", json=get_incidents)

    main()

    assert demisto.incidents.call_count == 1
    incidents = demisto.incidents.call_args[0][0]
    if last_run:
        assert len(incidents) == 8
        assert incidents[0]["occurred"] == "2021-08-03T20:25:13Z"
        assert incidents[1]["occurred"] == "2021-08-03T20:25:15Z"
        assert incidents_for_fetch == incidents
    else:
        assert not incidents


def test_fetch_incidents_input(mocker, client):
    """
    Test the inputs to the fetch incidents flow, and verify that they are of the correct format.
        Given:
            - Fetch params
        When:
            - Fetching incidents
        Then:
            - The input params are being sent in the correct format.
    """
    from SaasSecurity import fetch_incidents

    first_fetch_time = "1 day"
    fetch_limit = "1"
    fetch_severity = ["4.0", "5.0"]
    last_run = "2018-10-01T20:22:35.000Z"
    get_incidents = util_load_json("test_data/get-incidents.json")
    expected_params = {"from": "2018-10-01T20:22:35.000Z", "limit": "1", "severities": "4.0,5.0"}

    mocker.patch.object(demisto, "getLastRun", return_value={"last_run_time": last_run})
    http_request = mocker.patch.object(client, "http_request", return_value=get_incidents)

    fetch_incidents(client, first_fetch_time, fetch_limit, "", fetch_severity, "", "")
    http_request.assert_called_with("GET", url_suffix="/incident/api/incidents/delta", params=expected_params)


def test_get_incidents_input(mocker, client):
    """
    Test the inputs to the get_incidents_command, and verify that they are being sent in the correct format.
        Given:
            - The command arguments.
        When:
            - Running the command saas-security-incidents-get.
        Then:
            - The input params are being sent in the correct format.
    """
    from SaasSecurity import get_incidents_command

    limit = 1
    severity_old_format = ["4", "5"]
    get_incidents = util_load_json("test_data/get-incidents.json")
    args = {"limit": limit, "severity": severity_old_format}
    expected_params = {"limit": 10, "severities": "4.0,5.0", "state": "open"}

    http_request = mocker.patch.object(client, "http_request", return_value=get_incidents)

    get_incidents_command(client, args)
    http_request.assert_called_with("GET", url_suffix="/incident/api/incidents/delta", params=expected_params)


def test_get_incident_command_401(mocker, client):
    """Check that in a case of 401 error, a second call to get a forced access token is made.
    Given:
        - The command arguments.
    When:
        - Running the command saas-security-incidents-get.
    Then:
        - The function for generating an access token is called once.
    """
    from SaasSecurity import get_incidents_command

    class MockException:
        def __init__(self, status_code) -> None:
            self.status_code = status_code

    limit = 1
    severity_old_format = ["4", "5"]
    get_incidents = util_load_json("test_data/get-incidents.json")
    args = {"limit": limit, "severity": severity_old_format}
    res_401 = Response()
    res_401.status_code = 401
    res_401.reason = "Unauthorized"

    http_request = mocker.patch.object(
        BaseClient,
        "_http_request",
        side_effect=[DemistoException("Error in API call [401] - Unauthorized", res=MockException(401)), get_incidents],
    )

    get_incidents_command(client, args)

    assert http_request.call_count == 2


def test_get_incidents_command(client, requests_mock):
    """
    Using the client mocker, requests_mock.
       Given:
           - Command arguments.
       When:
           - Getting incidents with state and status set to 'All
       Then:
           - Sends request with the expected query params and created a CommandResult object.
    """
    from SaasSecurity import get_incidents_command

    incidents = util_load_json("test_data/get-incidents.json")
    req_mocker = requests_mock.get("http://base_url/incident/api/incidents/delta", json=incidents)

    result = get_incidents_command(client, {"limit": "5", "state": "All", "severity": "1.0,4.0", "status": "All"})

    assert all(param not in req_mocker.last_request.query for param in ("status", "state"))
    assert len(result.outputs.get("SaasSecurity.Incident(val.incident_id && val.incident_id == obj.incident_id)")) == 8


def test_get_incident_by_id_command(client, requests_mock):
    """
    Using the client mocker, requests_mock.
       Given:
           - Incident Id.
       When:
           - Getting incidents with state and status set to 'All'
       Then:
           - Sends request with the expected query params and creates a CommandResult object.
    """
    from SaasSecurity import get_incident_by_id_command

    incident = util_load_json("test_data/get-incident-by-id.json")
    requests_mock.get("http://base_url/incident/api/incidents/4", json=incident)
    res = get_incident_by_id_command(client, {"id": "4"})
    assert res.outputs == incident
    assert res.outputs_prefix == "SaasSecurity.Incident"


def test_update_incident_state_command(client, requests_mock):
    """
    Using the client mocker, requests_mock.
       Given:
           - Command arguments: Incident ID and category.
       When:
           - Updating an incident status.
       Then:
           - Sends request with the expected body and creates a CommandResult object.
    """
    from SaasSecurity import update_incident_state_command

    updated_status = util_load_json("test_data/update-incident-status.json")
    req_mocker = requests_mock.post("http://base_url/incident/api/incidents/4/state", json=updated_status)

    result = update_incident_state_command(client, {"id": "4", "category": "Business Justified"})

    assert all(param in req_mocker.last_request.text for param in ("business_justified", "state"))
    assert result.outputs.get("incident_id") == "4"


def test_remediate_asset_command(client, requests_mock):
    """
    Using the client mocker, requests_mock.
       Given:
           - Command arguments: Asset ID and remediation action.
       When:
           - Remediating an asset.
       Then:
           - Sends request with the expected query params and creates a CommandResult object.
    """

    from SaasSecurity import remediate_asset_command

    req_mocker = requests_mock.post("http://base_url/remediation/api/assets")
    result = remediate_asset_command(client, {"asset_id": "4", "remediation_type": "Remove public sharing"})

    assert "remove_public_sharing" in req_mocker.last_request.text
    assert result.outputs.get("asset_id") == "4"
    assert result.outputs.get("remediation_type") == "remove_public_sharing"
    assert result.outputs.get("status") == "pending"


@pytest.mark.xfail(
    raises=DemistoException,
    reason="Invalid remediation type: Invalid remediation.\n"
    "Must be one of the following: Remove public sharing, Quarantine, Restore",
)
def test_remediate_asset_command__invalid_action(client, requests_mock):
    """
    Configures mocker instance, requests_mock.
       Given:
           - Command arguments: Asset ID and invalid remediation action.
       When:
           - Remediating an asset.
       Then:
           - Raises a DemistoException.
    """
    from SaasSecurity import remediate_asset_command

    requests_mock.post("http://base_url/remediation/api/assets")
    remediate_asset_command(client, {"asset_id": "4", "remediation_type": "Invalid remediation"})


def test_get_remediation_status_command(client, requests_mock):
    """
    Using the client mocker, requests_mock.
       Given:
           - Command arguments: Asset ID and remediation action.
       When:
           - Remediating an asset.
       Then:
           - Sends request with the expected query params and creates a CommandResult object.
    """
    from SaasSecurity import get_remediation_status_command

    remediation_status = util_load_json("test_data/get-asset-remediation-status.json")
    req_mocker = requests_mock.get("http://base_url/remediation/api/assets", json=remediation_status)
    result = get_remediation_status_command(client, {"asset_id": "61099dd36b544e38fa3d22b9", "remediation_type": "Quarantine"})

    assert "system_quarantine" in req_mocker.last_request.query
    assert remediation_status == result.outputs


@pytest.mark.parametrize(
    "close_incident,expected_mirrored_object,expected_entries",
    [
        (
            False,
            {
                "category": "business_justified",
                "status": "Closed-Business Justified",
                "resolved_by": "api",
                "state": "closed",
                "asset_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            },
            [],
        ),
        (
            True,
            {
                "category": "business_justified",
                "status": "Closed-Business Justified",
                "resolved_by": "api",
                "state": "closed",
                "asset_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            },
            [
                {
                    "Type": EntryType.NOTE,
                    "ContentsFormat": EntryFormat.JSON,
                    "Contents": {"dbotIncidentClose": True, "closeReason": "From SaasSecurity: business_justified"},
                }
            ],
        ),
    ],
)
def test_get_remote_data_command(client, requests_mock, mocker, close_incident, expected_mirrored_object, expected_entries):
    from SaasSecurity import get_remote_data_command

    args = {"id": 1, "lastUpdate": "2021-08-24T07:44:21.608Z"}
    incident = util_load_json("test_data/get-incident-by-id.json")
    requests_mock.get("http://base_url/incident/api/incidents/1", json=incident)
    mocker.patch.object(demisto, "params", return_value={"close_incident": close_incident})

    result = get_remote_data_command(client, args)

    assert result.mirrored_object == expected_mirrored_object
    assert result.entries == expected_entries


@pytest.mark.parametrize(
    "close_incident,expected_mirrored_object,expected_entries",
    [
        (
            True,
            {
                "category": "business_justified",
                "status": "Closed-Business Justified",
                "resolved_by": "api",
                "state": "Closed",
                "asset_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            },
            [
                {
                    "Type": EntryType.NOTE,
                    "ContentsFormat": EntryFormat.JSON,
                    "Contents": {"dbotIncidentClose": True, "closeReason": "From SaasSecurity: business_justified"},
                }
            ],
        )
    ],
)
def test_get_remote_data_closed_status_uppercase(
    client, requests_mock, mocker, close_incident, expected_mirrored_object, expected_entries
):
    from SaasSecurity import get_remote_data_command

    args = {"id": 1, "lastUpdate": "2021-08-24T07:44:21.608Z"}
    incident = util_load_json("test_data/get-incident-by-id.json")
    incident["state"] = "Closed"

    requests_mock.get("http://base_url/incident/api/incidents/1", json=incident)
    mocker.patch.object(demisto, "params", return_value={"close_incident": close_incident})

    result = get_remote_data_command(client, args)

    assert result.mirrored_object == expected_mirrored_object
    assert result.entries == expected_entries


def test_get_modified_remote_data_command(client, requests_mock):
    from SaasSecurity import get_modified_remote_data_command

    args = {"lastUpdate": "2020-11-18T13:16:52.005381+02:00"}

    incidents = util_load_json("test_data/get-incidents.json")
    requests_mock.get("http://base_url/incident/api/incidents/delta", json=incidents)

    result = get_modified_remote_data_command(client, args)

    assert result.modified_incident_ids == ["3", "4", "5", "6", "7", "8", "9", "10"]


def test_get_mapping_fields_command():
    from SaasSecurity import get_mapping_fields_command

    result = get_mapping_fields_command().extract_mapping()

    assert result == {"Saas Security Incident": {"state": "", "category": ""}}


@pytest.mark.parametrize(
    "args,expected_debug_message",
    [
        (
            {
                "incidentChanged": True,
                "remoteId": "1",
                "data": {"state": "closed", "category": "No Reason"},
                "status": 1,
                "delta": {"category": "No Reason"},
                "entries": [],
            },
            "Incident updated successfully. Result: {'state': 'closed', 'category': 'No Reason'}",
        ),
        (
            {
                "incidentChanged": False,
                "remoteId": "2",
                "data": {"state": "closed", "category": "No Reason"},
                "status": 1,
                "delta": {"category": "No Reason"},
                "entries": [],
            },
            "Skipping updating remote incident fields [2] as it is not new nor changed.",
        ),
        (
            {
                "incidentChanged": True,
                "remoteId": "2",
                "data": {"state": "closed"},
                "status": 1,
                "delta": {"category": "No Reason"},
                "entries": [],
            },
            "Skipping updating the remote incident since the incident is not closed. "
            "Could not update the category for open incident due to an API limitation.",
        ),
        (
            {
                "incidentChanged": True,
                "remoteId": "2",
                "data": {"state": "open", "category": "No Reason"},
                "status": 1,
                "delta": {"category": "No Reason"},
                "entries": [],
            },
            "Skipping updating the remote incident since the incident is not closed. "
            "Could not update the category for open incident due to an API limitation.",
        ),
        (
            {
                "incidentChanged": True,
                "remoteId": "2",
                "data": {"state": "closed", "category": "Invalid Category"},
                "status": 1,
                "delta": {"category": "No Reason"},
                "entries": [],
            },
            "The value of category Invalid Category is invalid. "
            "The category can be one of the following ['no_reason', 'business_justified', 'misidentified'].",
        ),
        (
            {
                "incidentChanged": True,
                "remoteId": "2",
                "data": {"state": "closed", "category": "No Reason"},
                "status": 1,
                "delta": {"category": "No Reason"},
                "entries": [],
            },
            "Incident updated successfully. Result: {'state': 'closed', 'category': 'No Reason'}",
        ),
        (
            {
                "incidentChanged": True,
                "remoteId": "2",
                "data": {"state": "open", "category": "No Reason"},
                "status": 2,
                "delta": {"category": "No Reason"},
                "entries": [],
            },
            "Incident updated successfully. Result: {'state': 'open', 'category': 'No Reason'}",
        ),
    ],
)
def test_update_remote_system_command(requests_mock, mocker, client, args, expected_debug_message):
    from SaasSecurity import update_remote_system_command

    requests_mock.post(f'http://base_url/incident/api/incidents/{args.get("remoteId")}/state', json=args.get("data"))
    debug_result = mocker.patch.object(demisto, "debug")

    result = update_remote_system_command(client, args)

    assert result == args.get("remoteId")
    assert expected_debug_message in debug_result.call_args[0][0]


def test_validate_limit():
    """
    Test the validate_limit function behavior under various input conditions.

    Given: Different input values for the limit parameter.
    When: The validate_limit function is called with these inputs.
    Then: The function should return the expected validated limit values.

    Test cases:
    1. Negative limit: Should raise a DemistoException
    2. Limit less than 10: Should return LIMIT_MIN (10)
    3. Limit not divisible by 10: Should round down to nearest multiple of 10
    4. Limit greater than MAX_LIMIT: Should return LIMIT_MAX (200)
    5. No limit provided: Should return LIMIT_DEFAULT (50)
    6. Valid limit within range: Should return the input value
    7. Limits at boundaries: Should return LIMIT_MIN for 10 and LIMIT_MAX for 200
    """

    # Test with negative limit
    with pytest.raises(DemistoException, match="The limit parameter cannot be negative number or zero"):
        validate_limit(-1)

    # Test with limit less than 10
    assert validate_limit(5) == LIMIT_MIN

    # Test with limit not dividable by 10
    assert validate_limit(55) == 50

    # Test with limit greater than MAX_LIMIT
    assert validate_limit(250) == LIMIT_MAX

    # Test with no limit provided
    assert validate_limit(None) == LIMIT_DEFAULT

    # Test with valid limit
    assert validate_limit(100) == 100

    # Test with limit at boundaries
    assert validate_limit(10) == LIMIT_MIN
    assert validate_limit(200) == LIMIT_MAX
