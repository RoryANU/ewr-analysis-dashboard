# Databricks notebook source
# MAGIC %md
# MAGIC # Run your own scenarios through the EWR dashboard
# MAGIC
# MAGIC Runs py_ewr on your model output file(s) and writes a copy of the dashboard with the results built in. No server or command line is needed.
# MAGIC
# MAGIC 1. Upload your file(s) (a Unity Catalog volume, or the workspace). Note the path, e.g. `/Volumes/<catalog>/<schema>/<volume>/run.csv`.
# MAGIC 2. Edit `SCENARIOS` in the third cell, then **Run all**.
# MAGIC 3. The page is shown below and saved as `ewr_dashboard_with_scenarios.html`; download that file to open it in your browser.

# COMMAND ----------

# MAGIC %pip install py_ewr pandas numpy

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# Edit this list. "format" is one of: Standard time-series, Bigmod - MDBA, FIRM - MDBA, res.csv - MDBA,
# Source - NSW (res.csv), IQQM - netcdf, ten thousand year.  "name" is optional (defaults to the file name).
SCENARIOS = [
    {"name": "My scenario", "path": "/Volumes/<catalog>/<schema>/<volume>/my_run.csv", "format": "Standard time-series"},
    # {"name": "Wetter option", "path": "/Volumes/<catalog>/<schema>/<volume>/wetter.csv", "format": "Standard time-series"},
]
# Where the page is written. The default is the repository folder this notebook is in; use a /Volumes/... path to keep it elsewhere.
OUT_FILE = None

# COMMAND ----------

import os, sys
REPO = os.path.dirname(os.getcwd()) if os.path.basename(os.getcwd()) == "notebooks" else os.getcwd()   # the notebook's folder is <repo>/notebooks
sys.path.insert(0, os.path.join(REPO, "src"))
from embed_scenarios import build_with_scenarios

out = OUT_FILE or os.path.join(REPO, "ewr_dashboard_with_scenarios.html")
html = build_with_scenarios(SCENARIOS, out, template=os.path.join(REPO, "ewr_analysis_dashboard.html"))

# COMMAND ----------

# The dashboard, shown in the notebook (it loads its map libraries from the internet). Use the saved file for the full-size page.
displayHTML(html)
