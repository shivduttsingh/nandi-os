# Shiv (test branch) - quick audit, 2026-10-31

Verified by running the code (not by viewing the live site):
- 157/157 tests pass; app.py, shiv_app.py and pages/*.py compile.
- Codebases on this branch: `nandi_v2`, `nandi_oi`, `shiv_v1`, `shiv_v2`, plus entry points `app.py` and `shiv_app.py`.
- Pages: `1_A++++.py`, `6_NIFTY_Option_Charts.py`.
- Upstox access token is read in app.py, shiv_app.py, nandi_oi/*, and both pages. Views that need candles show "Not configured" without a token.
- No `.streamlit/config.toml` existed; one is added in this commit (white and green theme, usage stats off).

Open items before promoting anything to `main` (Nandi):
1. Confirm which entry file the Shiv site runs (`app.py` or `shiv_app.py`).
2. Persist evidence in git-tracked files; Streamlit Cloud disks are ephemeral.
3. Decide promotion gates (minimum recorded trades, expectancy > 0, profit factor > 1).
