from pathlib import Path

source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")

replacements = {
    'st.set_page_config(page_title="Nandi", page_icon="N"': 'st.set_page_config(page_title="Shiv", page_icon="S"',
    '<div class="eyebrow">Nandi Live</div>': '<div class="eyebrow">Shiv Live</div>',
    'header("Nandi", "Private NIFTY decision terminal': 'header("Shiv", "Private NIFTY decision terminal',
    'st.sidebar.markdown("## Nandi")': 'st.sidebar.markdown("## Shiv")',
    'Nandi decision': 'Shiv decision',
    'Nandi recalculates': 'Shiv recalculates',
    'Nandi confirms': 'Shiv confirms',
    "Nandi's": "Shiv's",
}

for old, new in replacements.items():
    source = source.replace(old, new)

exec(compile(source, "app.py", "exec"), globals(), globals())
