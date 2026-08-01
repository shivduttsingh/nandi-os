from nandi_oi.configuration import is_configured_value


def test_sample_placeholders_are_not_valid_configuration():
    assert not is_configured_value("")
    assert not is_configured_value(" YOUR_READ_ONLY_ANALYTICS_TOKEN ")
    assert not is_configured_value("paste_token_here")
    assert is_configured_value("real-private-value")
