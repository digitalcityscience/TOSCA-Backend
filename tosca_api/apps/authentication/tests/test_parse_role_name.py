"""Tests for parse_role_name -- ROLE_<ORG>[_<PROJECT>]_<LEVEL> grammar."""

import pytest

from tosca_api.apps.authentication.role_sync import ParsedRole, parse_role_name


def test_org_level_role():
    assert parse_role_name("ROLE_DCS_READER") == ParsedRole(
        org_slug="dcs", project="", level="READER"
    )


def test_project_scoped_role():
    assert parse_role_name("ROLE_DCS_TOSCA_WRITER") == ParsedRole(
        org_slug="dcs", project="tosca", level="WRITER"
    )


def test_org_and_project_are_lowercased():
    parsed = parse_role_name("ROLE_GQ2_ADMIN")
    assert parsed.org_slug == "gq2"
    assert parsed.level == "ADMIN"


@pytest.mark.parametrize(
    "name",
    [
        "",
        None,
        "DCS_READER",  # missing ROLE_ prefix
        "ROLE_DCS",  # no level segment
        "ROLE_READER",  # only a level, no org
        "ROLE_DCS_MANAGER",  # unknown level
        "ROLE_DCS_TOSCA_X_WRITER",  # >1 project segment (ambiguous) -> rejected
        "offline_access",
        "ROLE_DCS_READER_EXTRA",  # trailing segment is not a level
    ],
)
def test_non_conforming_names_return_none(name):
    assert parse_role_name(name) is None
