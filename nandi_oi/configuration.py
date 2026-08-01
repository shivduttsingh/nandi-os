from __future__ import annotations


_SAMPLE_PREFIXES = ("YOUR_", "PASTE_", "CHANGE_ME", "REPLACE_")


def is_configured_value(value: object) -> bool:
    """Return true only for a non-empty value that is not an example placeholder.

    The check deliberately never logs or returns the value itself.  It is used for
    local `.env` files as well as Streamlit Secrets so a copied sample file can
    never become a usable login or an accidental API token.
    """
    text = str(value or "").strip()
    return bool(text) and not text.upper().startswith(_SAMPLE_PREFIXES)

