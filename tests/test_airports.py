from flightintel.tools.airports import ResolveAirportInput, resolve_airport


def test_exact_icao_case_insensitive():
    r = resolve_airport(ResolveAirportInput(query="vtbs"))
    assert r.resolved is True
    assert r.matches[0].icao == "VTBS"
    assert r.matches[0].name == "Suvarnabhumi Airport"


def test_name_lookup():
    r = resolve_airport(ResolveAirportInput(query="Suvarnabhumi"))
    assert r.resolved is True
    assert r.matches[0].icao == "VTBS"


def test_alias_lookup():
    r = resolve_airport(ResolveAirportInput(query="Heathrow"))
    assert r.resolved is True
    assert r.matches[0].icao == "EGLL"


def test_ambiguous_city_returns_all_candidates_unresolved():
    r = resolve_airport(ResolveAirportInput(query="Bangkok"))
    assert r.resolved is False
    assert {m.icao for m in r.matches} == {"VTBS", "VTBD"}


def test_unknown_query_notes_honest_failure():
    r = resolve_airport(ResolveAirportInput(query="OM11"))
    assert r.resolved is False
    assert r.matches == []
    assert r.note is not None


def test_empty_query():
    r = resolve_airport(ResolveAirportInput(query="   "))
    assert r.resolved is False
    assert r.matches == []
