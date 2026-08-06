"""Nandi V2 background worker placeholder.

The old Upstox polling worker is intentionally disabled. Live research runs inside
the Streamlit app using the NSE adapter. A production worker may be enabled later
only with an authorised NSE Data feed and the same nandi_v2 decision models.
"""


def main() -> None:
    print("Nandi V2 worker is disabled: use the Streamlit app for NSE research mode.")


if __name__ == "__main__":
    main()
